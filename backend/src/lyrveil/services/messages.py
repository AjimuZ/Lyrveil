"""MessageService: sends messages, edits, deletes, regenerates replies into variants and pages through history.

A send saves first (docs/architecture.md, invariant 5): the user's message and the reply placeholder are committed in
one transaction, and the reply job is queued in a second one. A crash between the two leaves a message without a job,
and a retry with the same `client_message_id` queues the missing job without writing the message again. Deleting and
editing do not start derived-data work yet: the notification joins them with memory invalidation (P04.7).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.domain.conversations import Conversation, ConversationStatus
from lyrveil.domain.errors import ConflictError, NotFoundError, PayloadTooLargeError, ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.messages import (
    DEFAULT_PAGE_SIZE,
    MAX_MESSAGE_CHARS,
    MAX_PAGE_SIZE,
    PENDING,
    Author,
    JobRef,
    Message,
    MessageStatus,
    Page,
    Regeneration,
    SentMessage,
    Variant,
    VariantSource,
    VariantStatus,
    content_hash,
)
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import JobQueue, QueueFullError
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import conversations as conversation_repository
from lyrveil.persistence import messages as repository
from lyrveil.persistence.search import match_query, search_messages

_NO_CHAT = "Conversazione non trovata."
_NO_MESSAGE = "Messaggio non trovato."
_QUEUE_FULL = "Troppe richieste in coda: riprova tra poco."
_ONLY_LAST_REPLY = "Solo l'ultima risposta del personaggio può essere rigenerata o cambiata."
_BUSY = "Il messaggio è ancora in elaborazione."


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MessageService:
    """Reads and changes the messages of `engine`, and queues their reply jobs on `queue`.

    `clock` returns the aware current time; tests pass a fake one. A message of a deleted conversation, or a deleted
    message, is hidden from every method: it raises NotFoundError.
    """

    def __init__(self, engine: Engine, queue: JobQueue | None = None, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock
        self._queue = queue or JobQueue(clock=clock)

    def send(
        self, conversation_id: str, content: str, *, client_message_id: str | None = None, intent: str = "auto"
    ) -> SentMessage:
        """Saves the user's message and queues a `chat_reply` job for the character's reply.

        Returns the stored user message, the reply placeholder (status `queued`, empty text, one `streaming` variant
        the job fills) and the job. Sending the same `client_message_id` again to the conversation returns the first
        result and writes nothing new; if the first attempt died before queuing its job, the retry queues it.

        Raises NotFoundError for a missing or deleted conversation, ConflictError for an archived one, for the
        unavailable `image` intent, and when the queue is full (the message stays saved and the retry queues it),
        ValidationFailedError for blank text and PayloadTooLargeError over MAX_MESSAGE_CHARS.
        """
        _check_text(content)
        if intent == "image":
            raise ConflictError("La generazione di immagini dalla chat non è ancora disponibile.")
        now = to_utc_text(self._clock())

        def save(connection: Connection) -> tuple[Message, Message, str]:
            conversation = _visible_conversation(connection, conversation_id)
            if client_message_id is not None:
                stored = repository.find_by_client_id(connection, conversation_id, client_message_id)
                if stored is not None:
                    # The user message and its reply take consecutive numbers in one transaction.
                    reply = repository.find_by_seq(connection, conversation_id, stored.seq + 1)
                    assert reply is not None
                    assert reply.active_variant_id is not None
                    return stored, reply, conversation.character_id
            _require_active(conversation)
            user = _insert(
                connection,
                conversation_id,
                Author.USER,
                MessageStatus.COMPLETE,
                content,
                VariantSource.USER,
                VariantStatus.COMPLETE,
                now,
                client_message_id=client_message_id,
            )
            reply = _insert(
                connection,
                conversation_id,
                Author.CHARACTER,
                MessageStatus.QUEUED,
                "",
                VariantSource.GENERATED,
                VariantStatus.STREAMING,
                now,
            )
            return user, reply, conversation.character_id

        user, reply, character_id = write(self._engine, save)
        assert reply.active_variant_id is not None
        job = self._queue_job(
            JobKind.CHAT_REPLY,
            f"chat_reply:{user.id}",
            conversation_id,
            character_id,
            reply,
            {
                "user_message_id": user.id,
                "reply_message_id": reply.id,
                "variant_id": reply.active_variant_id,
                "intent": intent,
            },
        )
        return SentMessage(message=self._current(user.id), reply=self._current(reply.id), job=job)

    def history(self, conversation_id: str, before_seq: int | None = None, limit: int = DEFAULT_PAGE_SIZE) -> Page:
        """Returns the newest `limit` messages that are not deleted and have `seq` below `before_seq` (the newest of
        all when None), oldest first within the page. Pass the page's `next_before_seq` to get the older page.

        Messages still being produced are included with their status, so a reload recovers the whole chat state.
        Raises ValueError for a `limit` outside 1 to MAX_PAGE_SIZE and NotFoundError for a missing or deleted chat.
        """
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

        def load(connection: Connection) -> Page:
            _visible_conversation(connection, conversation_id)
            rows = repository.page(connection, conversation_id, before_seq, limit + 1)
            more = len(rows) > limit
            items = list(reversed(rows[:limit]))
            return Page(items=items, next_before_seq=items[0].seq if more else None)

        return read(self._engine, load)

    def search(self, conversation_id: str, q: str, limit: int = DEFAULT_PAGE_SIZE) -> list[Message]:
        """Returns up to `limit` messages of the chat, not deleted, whose text contains every word of `q` with accents
        ignored, newest first. `q` is plain text and never acts as search syntax; one with no letter or digit finds
        nothing.

        Raises ValueError for a `limit` outside 1 to MAX_PAGE_SIZE and NotFoundError for a missing or deleted chat.
        """
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        match = match_query(q)

        def load(connection: Connection) -> list[Message]:
            _visible_conversation(connection, conversation_id)
            return [] if match is None else search_messages(connection, conversation_id, match, limit)

        return read(self._engine, load)

    def edit(self, message_id: str, content: str) -> Message:
        """Replaces the text of a user or character message with a new `edited` variant, made active, and returns it.

        Editing to the text already shown changes nothing. An edited failed or interrupted reply becomes complete.
        Raises NotFoundError, ValidationFailedError and PayloadTooLargeError as `send` does, and ConflictError for a
        system message or one still being produced.
        """
        _check_text(content)
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> Message:
            message, _ = _visible_message(connection, message_id)
            if message.author is Author.SYSTEM:
                raise ConflictError("I messaggi di sistema non si modificano.")
            _require_settled(message)
            if content == message.content:
                return message
            variant_id = new_id()
            repository.insert_variant(
                connection,
                variant_id=variant_id,
                message_id=message.id,
                content=content,
                content_hash=content_hash(content),
                source=VariantSource.EDITED,
                status=VariantStatus.COMPLETE,
                now=now,
            )
            repository.update_message(
                connection,
                message.id,
                content=content,
                active_variant_id=variant_id,
                status=MessageStatus.COMPLETE.value,
                edited_at=now,
            )
            return _reload(connection, message.id)

        return write(self._engine, work)

    def delete(self, message_id: str) -> None:
        """Soft-deletes the message: it leaves the history and every read, and its row and variants stay stored.

        Raises NotFoundError when it does not exist or is already deleted, and ConflictError while it is queued or
        streaming.
        """
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> None:
            message, _ = _visible_message(connection, message_id)
            _require_settled(message)
            repository.update_message(connection, message.id, deleted_at=now)

        write(self._engine, work)

    def regenerate(self, message_id: str) -> Regeneration:
        """Adds a `generated` variant to the last character message, makes it the active one and queues a
        `chat_regenerate` job to fill it. The earlier variants stay as alternatives, selectable with
        `activate_variant`; the message is `queued` and its text empty until the job writes.

        Raises NotFoundError, and ConflictError when the message is not the last visible message of an active
        conversation, is not a character's, is still being produced, or the queue is full (the new variant is then
        marked failed and the older ones stay selectable).
        """
        now = to_utc_text(self._clock())

        def prepare(connection: Connection) -> tuple[Message, str, str, str]:
            message, conversation = _visible_message(connection, message_id)
            _require_active(conversation)
            _require_last_reply(connection, message)
            _require_settled(message)
            variant_id = new_id()
            repository.insert_variant(
                connection,
                variant_id=variant_id,
                message_id=message.id,
                content="",
                content_hash=content_hash(""),
                source=VariantSource.GENERATED,
                status=VariantStatus.STREAMING,
                now=now,
            )
            repository.update_message(
                connection, message.id, content="", active_variant_id=variant_id, status=MessageStatus.QUEUED.value
            )
            return _reload(connection, message.id), variant_id, conversation.character_id, conversation.id

        message, variant_id, character_id, conversation_id = write(self._engine, prepare)
        job = self._queue_job(
            JobKind.CHAT_REGENERATE,
            f"chat_regenerate:{variant_id}",
            conversation_id,
            character_id,
            message,
            {"message_id": message.id, "variant_id": variant_id},
        )
        return Regeneration(message=self._current(message.id), variant_id=variant_id, job=job)

    def variants(self, message_id: str) -> list[Variant]:
        """Returns every version of the message in creation order. Raises NotFoundError for a missing or deleted
        message."""

        def load(connection: Connection) -> list[Variant]:
            _visible_message(connection, message_id)
            return repository.list_variants(connection, message_id)

        return read(self._engine, load)

    def activate_variant(self, message_id: str, variant_id: str) -> Message:
        """Makes `variant_id` the shown version of the last character message and returns the message. Its text and
        status become the variant's. Choosing the active variant changes nothing.

        Raises NotFoundError for a missing message or a variant of another message, and ConflictError when the message
        is not the last visible message of its conversation, is not a character's, is still being produced, or the
        variant is still streaming.
        """

        def work(connection: Connection) -> Message:
            message, _ = _visible_message(connection, message_id)
            _require_last_reply(connection, message)
            _require_settled(message)
            variant = repository.get_variant(connection, variant_id)
            if variant is None or variant.message_id != message.id:
                raise NotFoundError("Variante non trovata.")
            if variant.id == message.active_variant_id:
                return message
            if variant.status is VariantStatus.STREAMING:
                raise ConflictError(_BUSY)
            repository.update_message(
                connection,
                message.id,
                content=variant.content,
                active_variant_id=variant.id,
                status=MessageStatus(variant.status.value).value,
            )
            return _reload(connection, message.id)

        return write(self._engine, work)

    def _current(self, message_id: str) -> Message:
        message = read(self._engine, lambda connection: repository.get_message(connection, message_id))
        assert message is not None
        return message

    def _queue_job(
        self,
        kind: JobKind,
        key: str,
        conversation_id: str,
        character_id: str,
        message: Message,
        payload: dict[str, Any],
    ) -> JobRef:
        """Queues the job for `message` under `key`, in its own transaction after the message was saved.

        A job already stored under `key` is returned as it is. When the queue is full nothing is queued: the message
        and its active variant are marked failed in another transaction, and ConflictError is raised. A failed
        message whose job did not exist, which an earlier full queue left, goes back to queued with its job.
        """

        def enqueue(connection: Connection) -> JobRef:
            existed = repository.job_exists(connection, key)
            job = self._queue.enqueue(
                connection,
                kind,
                payload,
                conversation_id=conversation_id,
                character_id=character_id,
                message_id=message.id,
                idempotency_key=key,
            )
            if not existed:
                stored = repository.get_message(connection, message.id)
                variant_id = None if stored is None else stored.active_variant_id
                if stored is not None and variant_id and stored.status is MessageStatus.FAILED:
                    repository.update_message(connection, message.id, status=MessageStatus.QUEUED.value)
                    repository.update_variant(connection, variant_id, status=VariantStatus.STREAMING.value)
            return JobRef(
                id=job.id,
                kind=job.kind.value,
                status=job.status.value,
                queue_position=repository.queue_position(connection, job.id),
            )

        try:
            return write(self._engine, enqueue)
        except QueueFullError:

            def fail(connection: Connection) -> None:
                repository.update_message(connection, message.id, status=MessageStatus.FAILED.value)
                if message.active_variant_id:
                    repository.update_variant(connection, message.active_variant_id, status=VariantStatus.FAILED.value)

            write(self._engine, fail)
            raise ConflictError(_QUEUE_FULL) from None


def _check_text(content: str) -> None:
    if not content.strip():
        raise ValidationFailedError("Il messaggio è vuoto.")
    if len(content) > MAX_MESSAGE_CHARS:
        raise PayloadTooLargeError(f"Il messaggio supera i {MAX_MESSAGE_CHARS} caratteri.")


def _visible_conversation(connection: Connection, conversation_id: str) -> Conversation:
    conversation = conversation_repository.get(connection, conversation_id)
    if conversation is None or conversation.status is ConversationStatus.DELETED:
        raise NotFoundError(_NO_CHAT)
    return conversation


def _visible_message(connection: Connection, message_id: str) -> tuple[Message, Conversation]:
    message = repository.get_message(connection, message_id)
    if message is None or message.deleted_at is not None:
        raise NotFoundError(_NO_MESSAGE)
    return message, _visible_conversation(connection, message.conversation_id)


def _require_active(conversation: Conversation) -> None:
    if conversation.status is not ConversationStatus.ACTIVE:
        raise ConflictError("La conversazione è archiviata.")


def _require_settled(message: Message) -> None:
    if message.status in PENDING:
        raise ConflictError(_BUSY)


def _require_last_reply(connection: Connection, message: Message) -> None:
    last = repository.last_visible(connection, message.conversation_id)
    if message.author is not Author.CHARACTER or last is None or last.id != message.id:
        raise ConflictError(_ONLY_LAST_REPLY)


def _reload(connection: Connection, message_id: str) -> Message:
    message = repository.get_message(connection, message_id)
    assert message is not None
    return message


def _insert(
    connection: Connection,
    conversation_id: str,
    author: Author,
    status: MessageStatus,
    content: str,
    source: VariantSource,
    variant_status: VariantStatus,
    now: str,
    *,
    client_message_id: str | None = None,
) -> Message:
    """Adds a message with its first variant, active, and returns it. Only a user message starts `sent`."""
    message_id = new_id()
    variant_id = new_id()
    repository.insert_message(
        connection,
        message_id=message_id,
        conversation_id=conversation_id,
        seq=repository.take_seq(connection, conversation_id, now),
        client_message_id=client_message_id,
        author=author,
        status=status,
        read_state="sent" if author is Author.USER else None,
        content=content,
        now=now,
    )
    repository.insert_variant(
        connection,
        variant_id=variant_id,
        message_id=message_id,
        content=content,
        content_hash=content_hash(content),
        source=source,
        status=variant_status,
        now=now,
    )
    repository.update_message(connection, message_id, active_variant_id=variant_id)
    return _reload(connection, message_id)
