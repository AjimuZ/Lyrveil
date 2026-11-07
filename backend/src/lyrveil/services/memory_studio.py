"""MemoryStudioService: what the Memory Studio reads (memories with their sources, conflicts and history, episodes,
summary checkpoints, the relationship state and its change log) and its small writes (lock, unlock, confirm and a
manual memory).

Reads open one read transaction. Each write is one write transaction, and a confirmation records its revision with the
change. Edits and deletions are MemoryService's. Every read names its owner (a character or a conversation), so one
chat's data never mixes with another's.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.consistency.state import DIMENSIONS, INITIAL_VALUES, band
from lyrveil.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.memory.text import fingerprint
from lyrveil.persistence import memory_studio as repository
from lyrveil.persistence.memory_extraction import NewMemory, insert_memory
from lyrveil.persistence.memory_index import index_memory, match_memory_ids
from lyrveil.persistence.memory_studio import MemoryFilter

DEFAULT_PAGE = 50
MAX_PAGE = 200
MAX_EPISODES = 200
MAX_HISTORY = 200
SEARCH_CANDIDATES = 500
MAX_CONTENT_CHARS = 280
# The kinds a user can write by hand. An `identity_candidate` only comes from extraction.
MANUAL_KINDS = (
    "user_fact",
    "user_preference",
    "character_fact",
    "narrative_event",
    "promise",
    "decision",
    "open_thread",
    "boundary",
)
_NO_MEMORY = "Il ricordo non esiste."
_NO_CHARACTER = "Il personaggio non esiste."
_NO_CONVERSATION = "La conversazione non esiste."
_BAD_CURSOR = "Il cursore non è valido."
_BAD_STATUS = "Lo stato richiesto non è valido."
_BAD_CONTENT = "Il testo del ricordo deve avere tra 1 e 280 caratteri."
_BAD_KIND = "Il tipo di ricordo non è valido."
_BAD_IMPORTANCE = "L'importanza deve essere un numero da 1 a 5."
_NOT_IN_USE = "Il ricordo non è in uso."
_DUPLICATE = "Esiste già un ricordo con lo stesso contenuto."
# docs/consistency-engine.md (From numbers to text): the four bands of each dimension, lowest first, worded from the
# user's side.
_BANDS: dict[str, tuple[str, str, str, str]] = {
    "familiarity": ("vi siete appena conosciuti", "vi state conoscendo", "vi conoscete bene", "vi conoscete a fondo"),
    "trust": ("diffida di te", "è prudente con te", "si fida di te", "si fida profondamente di te"),
    "confidence": (
        "parla poco di sé",
        "si apre su cose leggere",
        "ti racconta cose personali",
        "con te parla di tutto",
    ),
    "tension": ("nessuna tensione", "qualche attrito", "tensione evidente", "rapporto molto teso"),
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MemoryPage:
    """A page of the memory list and the cursor of the next one, None at the end."""

    items: list[dict[str, Any]]
    next_cursor: str | None


def _after(cursor: str | None) -> tuple[str, str] | None:
    if cursor is None:
        return None
    updated_at, separator, memory_id = cursor.partition("|")
    if not separator or not updated_at or not memory_id:
        raise ValidationFailedError(_BAD_CURSOR)
    return updated_at, memory_id


class MemoryStudioService:
    """Reads and small writes of the Memory Studio on `engine`. `clock` returns the aware current time."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def list_memories(
        self, filters: MemoryFilter, query: str | None, limit: int = DEFAULT_PAGE, cursor: str | None = None
    ) -> MemoryPage:
        """Returns a page of the character's memories that pass `filters`, the most recently changed first.

        `query` keeps the memories whose text or subject shares a stem with it, in any language the character's
        memories use; text without a searchable word matches nothing. `limit` is capped at 200. Raises NotFoundError
        for an unknown character and ValidationFailedError for a cursor this service did not give.
        """
        after = _after(cursor)
        size = max(1, min(limit, MAX_PAGE))

        def work(connection: Connection) -> MemoryPage:
            if not repository.character_exists(connection, filters.character_id):
                raise NotFoundError(_NO_CHARACTER)
            effective = filters
            if query is not None and query.strip():
                ids: set[str] = set()
                for language in repository.search_languages(connection, filters.character_id):
                    ids.update(match_memory_ids(connection, query, language, SEARCH_CANDIDATES))
                effective = replace(filters, ids=tuple(sorted(ids)))
            rows = repository.list_memories(connection, effective, size + 1, after)
            page = rows[:size]
            more = len(rows) > size
            return MemoryPage(page, f"{page[-1]['updated_at']}|{page[-1]['id']}" if more else None)

        return read(self._engine, work)

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        """Returns the memory with its `sources` (see `memory_sources`) and the other memories of its conflict group as
        `conflict`. Raises NotFoundError for an unknown memory."""
        return read(self._engine, lambda connection: self._detail(connection, memory_id))

    def revisions(self, memory_id: str) -> list[dict[str, Any]]:
        """Returns the memory's history, newest first. Raises NotFoundError for an unknown memory."""

        def work(connection: Connection) -> list[dict[str, Any]]:
            if repository.get_memory(connection, memory_id) is None:
                raise NotFoundError(_NO_MEMORY)
            return repository.memory_revisions(connection, memory_id)

        return read(self._engine, work)

    def set_locked(self, memory_id: str, locked: bool) -> dict[str, Any]:
        """Locks or unlocks the memory, which only stops or allows automation, and returns it as `get_memory` does.
        Locking a locked memory changes nothing. Raises NotFoundError for an unknown memory."""
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> dict[str, Any]:
            memory = repository.get_memory(connection, memory_id)
            if memory is None:
                raise NotFoundError(_NO_MEMORY)
            if memory["locked"] != locked:
                repository.set_locked(connection, memory_id, locked, now)
            return self._detail(connection, memory_id)

        return write(self._engine, work)

    def confirm(self, memory_id: str) -> dict[str, Any]:
        """Makes the memory `user_confirmed` with confidence 1.0 and returns it as `get_memory` does. The row as it was
        goes to the history first (actor `user`, reason `confirm`), unless it is already confirmed. Raises NotFoundError
        for an unknown memory and ConflictError for one that is not in use (active or uncertain)."""
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> dict[str, Any]:
            memory = repository.get_memory(connection, memory_id)
            if memory is None:
                raise NotFoundError(_NO_MEMORY)
            if memory["status"] not in ("active", "uncertain"):
                raise ConflictError(_NOT_IN_USE)
            if memory["provenance"] != "user_confirmed":
                repository.confirm(connection, memory_id, now)
            return self._detail(connection, memory_id)

        return write(self._engine, work)

    def create(self, conversation_id: str, content: str, kind: str, importance: int) -> dict[str, Any]:
        """Adds a memory the user wrote to the conversation: provenance `user_confirmed`, confidence 1.0, lasting,
        active, in the conversation's language, with no source message. Returns it as `get_memory` does.

        Raises NotFoundError for an unknown conversation, ValidationFailedError for a text outside 1 to 280 characters,
        a kind that is not one of MANUAL_KINDS or an importance outside 1 to 5, and ConflictError when a memory in use
        of the conversation already has the same fingerprint.
        """
        text_ = content.strip()
        if not 1 <= len(text_) <= MAX_CONTENT_CHARS:
            raise ValidationFailedError(_BAD_CONTENT)
        if kind not in MANUAL_KINDS:
            raise ValidationFailedError(_BAD_KIND)
        if not 1 <= importance <= 5:
            raise ValidationFailedError(_BAD_IMPORTANCE)
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> dict[str, Any]:
            owner = repository.conversation_owner(connection, conversation_id)
            if owner is None:
                raise NotFoundError(_NO_CONVERSATION)
            character_id, language = owner
            digest = fingerprint(text_, language)
            if repository.fingerprint_taken(connection, character_id, conversation_id, digest):
                raise ConflictError(_DUPLICATE)
            memory_id = new_id()
            insert_memory(
                connection,
                NewMemory(
                    id=memory_id,
                    character_id=character_id,
                    conversation_id=conversation_id,
                    kind=kind,
                    content=text_,
                    subject_key=None,
                    value_text=None,
                    provenance="user_confirmed",
                    durability="lasting",
                    importance=importance,
                    confidence=1.0,
                    valid_from=now,
                    expires_at=None,
                    fingerprint=digest,
                    language=language,
                ),
                now,
            )
            index_memory(connection, memory_id, text_, None, language)
            return self._detail(connection, memory_id)

        return write(self._engine, work)

    def list_episodes(
        self, character_id: str, conversation_id: str | None, query: str | None, status: str = "active"
    ) -> list[dict[str, Any]]:
        """Returns the character's episodes in `status` (`active` or `invalidated`), the most recent first, up to 200,
        each with its participants, source messages and links. `query` keeps those that share a stem with it. Raises
        NotFoundError for an unknown character."""
        if status not in ("active", "invalidated"):
            raise ValidationFailedError(_BAD_STATUS)

        def work(connection: Connection) -> list[dict[str, Any]]:
            if not repository.character_exists(connection, character_id):
                raise NotFoundError(_NO_CHARACTER)
            ids = None
            if query is not None and query.strip():
                languages = repository.search_languages(connection, character_id)
                ids = repository.matching_episode_ids(connection, query, languages, SEARCH_CANDIDATES)
            return repository.list_episodes(connection, character_id, conversation_id, status, ids, MAX_EPISODES)

        return read(self._engine, work)

    def summaries(self, conversation_id: str) -> list[dict[str, Any]]:
        """Returns every summary checkpoint of the conversation in any status, oldest range first. Raises NotFoundError
        for an unknown or deleted conversation."""

        def work(connection: Connection) -> list[dict[str, Any]]:
            _require_conversation(connection, conversation_id)
            return repository.conversation_summaries(connection, conversation_id)

        return read(self._engine, work)

    def relationship(self, conversation_id: str) -> dict[str, Any]:
        """Returns the conversation's relationship state: `written` (false before any run wrote one), the narrative,
        expectations, boundaries, emotional state, topic, open threads, goals, narrative changes, recent events, the
        `description` in words, the `bands` per dimension and the four `values`, which the Studio shows as advanced.
        Raises NotFoundError for an unknown or deleted conversation."""

        def work(connection: Connection) -> dict[str, Any]:
            _require_conversation(connection, conversation_id)
            return _relationship(repository.relationship_row(connection, conversation_id))

        return read(self._engine, work)

    def relationship_history(self, conversation_id: str) -> list[dict[str, Any]]:
        """Returns the newest 200 rows of the conversation's change log, each with its reason and source messages.
        Raises NotFoundError for an unknown or deleted conversation."""

        def work(connection: Connection) -> list[dict[str, Any]]:
            _require_conversation(connection, conversation_id)
            return repository.state_history(connection, conversation_id, MAX_HISTORY)

        return read(self._engine, work)

    @staticmethod
    def _detail(connection: Connection, memory_id: str) -> dict[str, Any]:
        memory = repository.get_memory(connection, memory_id)
        if memory is None:
            raise NotFoundError(_NO_MEMORY)
        memory["sources"] = repository.memory_sources(connection, memory_id)
        memory["conflict"] = repository.conflict_peers(connection, memory)
        return memory


def _require_conversation(connection: Connection, conversation_id: str) -> None:
    if not repository.conversation_exists(connection, conversation_id):
        raise NotFoundError(_NO_CONVERSATION)


def _relationship(row: dict[str, Any] | None) -> dict[str, Any]:
    """Shapes the stored state for the Studio. Without a row every list is empty and the values are the starting ones
    of an empty state, which the caller can tell by `written` being false."""
    if row is None:
        row = {
            **INITIAL_VALUES,
            "narrative": "",
            "expectations": "",
            "boundaries": "",
            "emotional_state": None,
            "current_topic": None,
            "open_threads": [],
            "goals": [],
            "narrative_changes": [],
            "recent_events": [],
            "updated_at": None,
        }
        written = False
    else:
        written = True
    bands = {dimension: _BANDS[dimension][band(row[dimension])] for dimension in DIMENSIONS}
    description = row["narrative"] or (", ".join(bands[dimension] for dimension in DIMENSIONS) if written else "")
    return {
        "written": written,
        "description": description,
        "narrative": row["narrative"],
        "expectations": row["expectations"],
        "boundaries": row["boundaries"],
        "emotional_state": row["emotional_state"],
        "current_topic": row["current_topic"],
        "open_threads": row["open_threads"],
        "goals": row["goals"],
        "narrative_changes": row["narrative_changes"],
        "recent_events": row["recent_events"],
        "bands": bands,
        "values": {dimension: row[dimension] for dimension in DIMENSIONS},
        "updated_at": row["updated_at"],
    }
