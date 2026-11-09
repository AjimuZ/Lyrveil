"""ConversationService: creates, lists, changes, deletes and restores conversations.

Deletion follows docs/data-model.md (Deletion policy): a delete only sets `status = deleted` and `deleted_at`, a
restore within RESTORE_WINDOW undoes it, and no row leaves durable history.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.domain.conversations import RESTORE_WINDOW, Conversation, ConversationStatus
from lyrveil.domain.errors import ConflictError, NotFoundError
from lyrveil.domain.identifiers import from_utc_text, new_id, to_utc_text
from lyrveil.domain.messages import Author, MessageStatus, VariantSource, VariantStatus, content_hash
from lyrveil.domain.scenarios import ScenarioStatus
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import conversations as repository
from lyrveil.persistence import messages as message_repository
from lyrveil.persistence import scenarios as scenario_repository
from lyrveil.persistence.search import match_query
from lyrveil.scheduling.store import cancel_pending_events

# The fields PATCH may change (docs/api.md, Conversations).
PATCHABLE = frozenset({"title", "pinned", "archived", "language", "shares_memory"})
_NO_CHAT = "Conversazione non trovata."


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _write_opening_message(connection: Connection, conversation_id: str, content: str, now: str) -> None:
    """Adds the scenario's opening message as the chat's first message: a complete character message with one
    generated variant, as a reply leaves it, so history, search and the prompt treat it like any other turn."""
    message_id, variant_id = new_id(), new_id()
    message_repository.insert_message(
        connection,
        message_id=message_id,
        conversation_id=conversation_id,
        seq=message_repository.take_seq(connection, conversation_id, now),
        client_message_id=None,
        author=Author.CHARACTER,
        status=MessageStatus.COMPLETE,
        read_state=None,
        content=content,
        now=now,
    )
    message_repository.insert_variant(
        connection,
        variant_id=variant_id,
        message_id=message_id,
        content=content,
        content_hash=content_hash(content),
        source=VariantSource.GENERATED,
        status=VariantStatus.COMPLETE,
        now=now,
    )
    message_repository.update_message(connection, message_id, active_variant_id=variant_id)


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
        language to the scenario's, else to inheriting the character's. A character can have any number of
        conversations; each one has its own history, so none reads another's.

        With a `scenario_id` the conversation stores a copy of the scenario (Scenario.snapshot), and a non-empty
        opening message becomes its first message, written by the character. Raises NotFoundError when the character,
        or the scenario, does not exist, and ConflictError when the character is archived, the scenario is archived,
        or the scenario is for another character.
        """
        now = to_utc_text(self._clock())

        def work(connection: Any) -> Conversation:
            character = repository.find_character(connection, character_id)
            if character is None:
                raise NotFoundError("Personaggio non trovato.")
            if not character.active:
                raise ConflictError("Il personaggio è archiviato.")
            scenario = None if scenario_id is None else scenario_repository.get(connection, scenario_id)
            if scenario_id is not None:
                if scenario is None:
                    raise NotFoundError("Scenario non trovato.")
                if scenario.status is ScenarioStatus.ARCHIVED:
                    raise ConflictError("Lo scenario è archiviato.")
                if scenario.character_id not in (None, character.id):
                    raise ConflictError("Lo scenario è pensato per un altro personaggio.")
            conversation_id = new_id()
            repository.insert(
                connection,
                conversation_id=conversation_id,
                character_id=character.id,
                title=(title or "").strip() or character.name,
                language=language or (scenario.language if scenario else None),
                shares_memory=shares_memory,
                now=now,
                scenario_id=scenario_id,
                scenario_snapshot=None if scenario is None else scenario.snapshot(),
            )
            if scenario is not None and scenario.opening_message:
                _write_opening_message(connection, conversation_id, scenario.opening_message, now)
            created = repository.get(connection, conversation_id)
            assert created is not None
            return created

        return write(self._engine, work)

    def list_conversations(
        self,
        status: ConversationStatus = ConversationStatus.ACTIVE,
        character_id: str | None = None,
        q: str | None = None,
    ) -> list[Conversation]:
        """Returns the conversations in `status`, pinned first and then by latest activity.

        A non-blank `q` keeps those whose title or message text contains every word of it, with accents ignored. `q`
        is plain text: it never acts as search syntax. A `q` with no letter or digit matches nothing.
        """
        match = None if q is None or not q.strip() else match_query(q)
        if q is not None and q.strip() and match is None:
            return []
        return read(self._engine, lambda connection: repository.list_all(connection, status, character_id, match))

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

        Its `pending` scheduled events become `cancelled` in the same transaction, so a restore does not bring back
        proactive messages that fell due meanwhile.
        """
        now = to_utc_text(self._clock())

        def work(connection: Any) -> None:
            current = repository.get(connection, conversation_id)
            if current is None or current.status is ConversationStatus.DELETED:
                raise NotFoundError(_NO_CHAT)
            repository.update(connection, conversation_id, now, status=ConversationStatus.DELETED.value, deleted_at=now)
            cancel_pending_events(connection, conversation_id, now)

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
