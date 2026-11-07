"""MemoryService: the user's own changes to a memory, an edit that records a revision and a deletion that leaves a
tombstone (docs/memory-engine.md, Deletion and tombstones; ADR 0012).

Each operation is one write transaction. An edit never loses the earlier text: the whole row goes to
`memory_revisions` first. A deletion removes the text, the revisions and the sources, and keeps hashes only, so
extraction cannot bring the memory back from the same messages.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine

from lyrveil.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.jobs.scheduler import write
from lyrveil.memory import consistency
from lyrveil.memory.text import fingerprint
from lyrveil.persistence import memories as repository
from lyrveil.persistence import memory_extraction
from lyrveil.persistence.memories import StoredMemory
from lyrveil.persistence.memory_index import index_memory

MAX_CONTENT_CHARS = 280
MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 5
_NO_MEMORY = "Il ricordo non esiste."
_BAD_CONTENT = "Il testo del ricordo deve avere tra 1 e 280 caratteri."
_BAD_SUBJECT = "L'argomento del ricordo non è valido."
_BAD_VALUE = "Il valore del ricordo non è valido."
_BAD_IMPORTANCE = "L'importanza deve essere un numero da 1 a 5."
_DUPLICATE = "Esiste già un ricordo con lo stesso contenuto."


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MemoryEdit:
    """The fields a user edit changes; None leaves a field as it is (a subject or value cannot be cleared here)."""

    content: str | None = None
    subject: str | None = None
    value: str | None = None
    importance: int | None = None


class MemoryService:
    """Edits and deletes memories of `engine`. `clock` returns the aware current time; tests pass a fake one."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def edit(self, memory_id: str, change: MemoryEdit) -> StoredMemory:
        """Applies `change` and returns the memory as stored. When something differs, the row as it was is appended to
        `memory_revisions` (actor `user`, reason `edit`, under its version), the memory moves to the next version, its
        fingerprint follows the new text and its index row is refreshed. Status, provenance, lock and sources are kept,
        and a locked memory can be edited: the lock only stops automation. A change that alters nothing writes nothing.

        Raises NotFoundError for an unknown memory, ValidationFailedError for text outside 1 to 280 characters, a
        blank subject or value, or an importance outside 1 to 5, and ConflictError when the new text is the same as
        another memory in use of the same owner. Nothing is written in those cases.
        """
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> StoredMemory:
            memory = repository.get(connection, memory_id)
            if memory is None:
                raise NotFoundError(_NO_MEMORY)
            content = memory.content if change.content is None else _checked_content(change.content)
            subject = memory.subject_key if change.subject is None else _checked_subject(change.subject)
            value = memory.value_text if change.value is None else _checked_value(change.value)
            importance = memory.importance if change.importance is None else _checked_importance(change.importance)
            if (content, subject, value, importance) == (
                memory.content,
                memory.subject_key,
                memory.value_text,
                memory.importance,
            ):
                return memory
            new_fingerprint = memory.fingerprint if content == memory.content else fingerprint(content, memory.language)
            in_use = memory.status in ("active", "uncertain")
            if (
                in_use
                and new_fingerprint != memory.fingerprint
                and repository.fingerprint_in_use(connection, memory, new_fingerprint)
            ):
                raise ConflictError(_DUPLICATE)
            repository.edit(
                connection,
                memory,
                content=content,
                subject_key=subject,
                value_text=value,
                importance=importance,
                fingerprint=new_fingerprint,
                now=now,
            )
            index_memory(connection, memory.id, content, subject, memory.language)
            edited = repository.get(connection, memory.id)
            assert edited is not None, "the memory was just edited"
            return edited

        return write(self._engine, work)

    def delete(self, memory_id: str) -> None:
        """Deletes the memory and leaves its tombstone, in one transaction.

        The memory row, its revisions, sources and index row are removed. The tombstone keeps the fingerprint, subject
        key, value hash, the content hashes of the sources and the seq of the newest source, and no text. A memory with
        no source (a manual one) uses the conversation's extraction cursor, so no message already read can bring it
        back. Raises NotFoundError for an unknown memory.
        """
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> None:
            memory = repository.get(connection, memory_id)
            if memory is None:
                raise NotFoundError(_NO_MEMORY)
            newest, hashes = repository.source_facts(connection, memory.id)
            if newest is None:
                read = (
                    0
                    if memory.conversation_id is None
                    else memory_extraction.cursor(connection, memory.conversation_id)
                )
                newest = max(1, read)
            tombstone = consistency.Tombstone(
                fingerprint=memory.fingerprint,
                subject_key=memory.subject_key,
                value_hash=consistency.value_hash(memory.value_text, memory.language),
                source_hashes=frozenset(hashes),
                source_max_seq=newest,
            )
            repository.insert_tombstone(connection, memory, tombstone, now)
            repository.delete(connection, memory.id)

        write(self._engine, work)


def _checked_content(content: str) -> str:
    trimmed = content.strip()
    if not 1 <= len(trimmed) <= MAX_CONTENT_CHARS:
        raise ValidationFailedError(_BAD_CONTENT)
    return trimmed


def _checked_subject(subject: str) -> str:
    key = consistency.subject_key(subject)
    if key is None:
        raise ValidationFailedError(_BAD_SUBJECT)
    return key


def _checked_value(value: str) -> str:
    stored = consistency.value_text(value)
    if stored is None:
        raise ValidationFailedError(_BAD_VALUE)
    return stored


def _checked_importance(importance: int) -> int:
    if not MIN_IMPORTANCE <= importance <= MAX_IMPORTANCE:
        raise ValidationFailedError(_BAD_IMPORTANCE)
    return importance
