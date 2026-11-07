"""ConversationService: creates, lists, changes, deletes and restores conversations.

Deletion follows docs/data-model.md (Deletion policy): a delete only sets `status = deleted` and `deleted_at`, a
restore within RESTORE_WINDOW undoes it, and no row leaves durable history.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from lyrveil.domain.conversations import RESTORE_WINDOW, Conversation, ConversationStatus
from lyrveil.domain.errors import ConflictError, NotFoundError
from lyrveil.domain.identifiers import from_utc_text, new_id, to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import conversations as repository

# The fields PATCH may change (docs/api.md, Conversations).
PATCHABLE = frozenset({"title", "pinned", "archived", "language", "shares_memory"})
_NO_CHAT = "Conversazione non trovata."


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ConversationService:
    """Reads and changes conversations of `engine`. `clock` returns the aware current time; tests pass a fake one.

    Every method that finds no visible conversation raises NotFoundError: a deleted conversation is hidden from
    everything except `restore`.
    """

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def create(
        self,
        character_id: str,
        *,
        scenario_id: str | None = None,
        title: str | None = None,
        language: str | None = None,
        shares_memory: bool = False,
    ) -> Conversation:
        """Returns a new active conversation with the character. The title defaults to the character's name and the
        language to inheriting the character's.

        Raises NotFoundError when the character, or the scenario, does not exist (no scenario can exist before the
        scenarios table does), and ConflictError when the character is archived.
        """
        if scenario_id is not None:
            raise NotFoundError("Scenario non trovato.")
        now = to_utc_text(self._clock())

        def work(connection: Any) -> Conversation:
            character = repository.find_character(connection, character_id)
            if character is None:
                raise NotFoundError("Personaggio non trovato.")
            if not character.active:
                raise ConflictError("Il personaggio è archiviato.")
            conversation_id = new_id()
            repository.insert(
                connection,
                conversation_id=conversation_id,
                character_id=character.id,
                title=(title or "").strip() or character.name,
                language=language,
                shares_memory=shares_memory,
                now=now,
            )
            created = repository.get(connection, conversation_id)
            assert created is not None
            return created

        return write(self._engine, work)

    def list_conversations(
        self, status: ConversationStatus = ConversationStatus.ACTIVE, character_id: str | None = None
    ) -> list[Conversation]:
        """Returns the conversations in `status`, pinned first and then by latest activity."""
        return read(self._engine, lambda connection: repository.list_all(connection, status, character_id))

    def get(self, conversation_id: str) -> Conversation:
        """Returns the conversation unless it is deleted; raises NotFoundError otherwise."""
        found = read(self._engine, lambda connection: repository.get(connection, conversation_id))
        if found is None or found.status is ConversationStatus.DELETED:
            raise NotFoundError(_NO_CHAT)
        return found

    def update(self, conversation_id: str, changes: Mapping[str, Any]) -> Conversation:
        """Applies the PATCH `changes` and returns the conversation.

        `changes` holds only the fields to change, from PATCHABLE: `title` (blank restores the character's name),
        `pinned` (a new pin takes the current time and an existing one is kept), `archived`, `language` (None
        inherits) and `shares_memory`. Raises ValueError for any other key.
        """
        unknown = set(changes) - PATCHABLE
        if unknown:
            raise ValueError(f"not patchable: {sorted(unknown)}")
        now = to_utc_text(self._clock())

        def work(connection: Any) -> Conversation:
            current = repository.get(connection, conversation_id)
            if current is None or current.status is ConversationStatus.DELETED:
                raise NotFoundError(_NO_CHAT)
            columns: dict[str, object] = {}
            if "title" in changes:
                columns["title"] = (changes["title"] or "").strip() or current.character_name
            if "pinned" in changes:
                columns["pinned_at"] = (current.pinned_at or now) if changes["pinned"] else None
            if "archived" in changes:
                columns["status"] = (
                    ConversationStatus.ARCHIVED if changes["archived"] else ConversationStatus.ACTIVE
                ).value
            if "language" in changes:
                columns["language"] = changes["language"]
            if "shares_memory" in changes:
                columns["shares_memory"] = int(bool(changes["shares_memory"]))
            if columns:
                repository.update(connection, conversation_id, now, **columns)
            updated = repository.get(connection, conversation_id)
            assert updated is not None
            return updated

        return write(self._engine, work)

    def delete(self, conversation_id: str) -> None:
        """Soft-deletes the conversation: it disappears from every list and read, and `restore` brings it back for
        RESTORE_WINDOW. Raises NotFoundError when it does not exist or is already deleted.

        Cancelling its scheduled events joins here when `scheduled_events` exists (P07).
        """
        now = to_utc_text(self._clock())

        def work(connection: Any) -> None:
            current = repository.get(connection, conversation_id)
            if current is None or current.status is ConversationStatus.DELETED:
                raise NotFoundError(_NO_CHAT)
            repository.update(connection, conversation_id, now, status=ConversationStatus.DELETED.value, deleted_at=now)

        write(self._engine, work)

    def restore(self, conversation_id: str) -> Conversation:
        """Returns the deleted conversation made active again, with its pin and every other field kept.

        The status before the delete is not stored, so an archived chat comes back active. Raises NotFoundError when
        no such conversation exists and ConflictError when it is not deleted or its RESTORE_WINDOW has passed.
        """
        moment = self._clock()
        now = to_utc_text(moment)

        def work(connection: Any) -> Conversation:
            current = repository.get(connection, conversation_id)
            if current is None:
                raise NotFoundError(_NO_CHAT)
            if current.status is not ConversationStatus.DELETED or current.deleted_at is None:
                raise ConflictError("La conversazione non è eliminata.")
            if moment - from_utc_text(current.deleted_at) > RESTORE_WINDOW:
                raise ConflictError("Sono passati più di 30 giorni dall'eliminazione.")
            repository.update(connection, conversation_id, now, status=ConversationStatus.ACTIVE.value, deleted_at=None)
            restored = repository.get(connection, conversation_id)
            assert restored is not None
            return restored

        return write(self._engine, work)
