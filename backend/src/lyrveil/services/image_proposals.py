"""ImageProposalService: contextual image proposals, which stay off until the user switches them on and never draw
anything without an answer (docs/image-generation.md, Contextual mode).

`queue_if_due` decides, from the settings and the chat, whether an `image_proposal` job may be queued. `propose` stores
the job's validated decision as a `pending` proposal and announces it with `image.proposed`; only a decision that says
`propose`, whose text passes the content guard and whose conversation and message still exist is stored. `accept`
queues the `image_request` job together with the status change, and `reject` closes the proposal; an answer to a
proposal that is not pending, or that sat unanswered for PROPOSAL_TTL_HOURS, changes nothing and raises ConflictError.

Delivered here: the gate, the storage and the two answers. The model step that writes the decision (the
`image_proposal` job handler) is not part of this module.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from anyio import to_thread
from sqlalchemy import Connection, Engine

from lyrveil.consistency.content_guard import find_violation
from lyrveil.domain.conversations import ConversationStatus
from lyrveil.domain.errors import ConflictError, NotFoundError
from lyrveil.domain.identifiers import from_utc_text, new_id, to_utc_text
from lyrveil.domain.image_proposals import (
    DEFAULT_MIN_MESSAGES,
    MAX_MIN_MESSAGES,
    MIN_MIN_MESSAGES,
    PROPOSAL_COOLDOWN_MINUTES,
    PROPOSAL_TTL_HOURS,
    ImageProposalResult,
)
from lyrveil.domain.messages import JobRef, SentMessage
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import JobQueue, QueueFullError
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.persistence import conversations as conversation_repository
from lyrveil.persistence import image_proposals as repository
from lyrveil.persistence import messages as message_repository
from lyrveil.persistence import settings as settings_repository
from lyrveil.ports.events import IMAGE_PROPOSED, EventPublisher
from lyrveil.services.images import ImageService

_NO_PROPOSAL = "Proposta non trovata."
_DECIDED = "La proposta ha già una risposta."
_EXPIRED = "La proposta è scaduta."
_QUEUE_FULL = "Troppe richieste in coda: riprova tra poco."

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ImageProposalService:
    """Gates, stores and answers the contextual image proposals of `engine`.

    `images` queues the image of an accepted proposal, `events` receives `image.proposed`, `min_messages` is
    `images.proposal_min_messages` (MIN_MIN_MESSAGES to MAX_MIN_MESSAGES, else ValueError) and `clock` returns the
    aware current time; tests pass a fake.
    """

    def __init__(
        self,
        engine: Engine,
        images: ImageService,
        events: EventPublisher,
        *,
        queue: JobQueue | None = None,
        min_messages: int = DEFAULT_MIN_MESSAGES,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not MIN_MIN_MESSAGES <= min_messages <= MAX_MIN_MESSAGES:
            raise ValueError(f"min_messages must be between {MIN_MIN_MESSAGES} and {MAX_MIN_MESSAGES}")
        self._engine = engine
        self._images = images
        self._events = events
        self._queue = queue or JobQueue(clock=clock)
        self._min_messages = min_messages
        self._clock = clock

    def queue_if_due(self, connection: Connection, conversation_id: str) -> JobRef | None:
        """Queues an `image_proposal` job for the conversation in the caller's transaction, and returns it, when a
        proposal is due; otherwise returns None and writes nothing.

        Due means: `images.contextual_enabled` is on (it is off by default); the conversation is active; no proposal
        waits for an answer and no `image_proposal` job is queued or running; the newest proposal is at least
        PROPOSAL_COOLDOWN_MINUTES old and `min_messages` messages came after the message it followed (all the messages
        when there was none). Raises QueueFullError when the queue is full.
        """
        if not settings_repository.get_flag(connection, settings_repository.IMAGES_CONTEXTUAL_ENABLED, False):
            return None
        conversation = conversation_repository.get(connection, conversation_id)
        if conversation is None or conversation.status is not ConversationStatus.ACTIVE:
            return None
        if repository.has_pending(connection, conversation_id) or repository.job_active(connection, conversation_id):
            return None
        after = 0
        previous = repository.latest(connection, conversation_id)
        if previous is not None:
            created, after = previous
            if self._clock() - from_utc_text(created) < timedelta(minutes=PROPOSAL_COOLDOWN_MINUTES):
                return None
        if repository.messages_after(connection, conversation_id, after) < self._min_messages:
            return None
        last = message_repository.last_visible(connection, conversation_id)
        if last is None:
            return None
        job = self._queue.enqueue(
            connection,
            JobKind.IMAGE_PROPOSAL,
            {"message_id": last.id},
            conversation_id=conversation_id,
            character_id=conversation.character_id,
            message_id=last.id,
            idempotency_key=f"image_proposal:{last.id}",
        )
        return JobRef(
            id=job.id,
            kind=job.kind.value,
            status=job.status.value,
            queue_position=message_repository.queue_position(connection, job.id),
        )

    async def propose(
        self, conversation_id: str, message_id: str, result: ImageProposalResult
    ) -> dict[str, Any] | None:
        """Stores `result` as a pending proposal after the message `message_id` and publishes `image.proposed` once
        committed. Returns the proposal's document, or None when nothing was stored (see `record`).

        Must run on the event loop, like every publisher call.
        """
        proposal = await to_thread.run_sync(self.record, conversation_id, message_id, result)
        if proposal is not None:
            self._events.publish(IMAGE_PROPOSED, {"proposal": proposal})
            logger.info("image_proposed", proposal_id=proposal["id"])
        return proposal

    def record(self, conversation_id: str, message_id: str, result: ImageProposalResult) -> dict[str, Any] | None:
        """Stores `result` as a `pending` proposal and returns its document, or returns None and stores nothing.

        Nothing is stored when contextual mode was switched off meanwhile, `result.propose` is false or its scene is
        blank, the content guard refuses the scene or the reason, the conversation is not active, the message is not a
        live message of it, or a proposal already waits for an answer. The refusal never logs the text.
        """
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> dict[str, Any] | None:
            if not settings_repository.get_flag(connection, settings_repository.IMAGES_CONTEXTUAL_ENABLED, False):
                return None
            if not result.propose or not result.scene:
                return None
            if find_violation(result.scene) is not None or find_violation(result.reason) is not None:
                logger.info("image_proposal_blocked")
                return None
            conversation = conversation_repository.get(connection, conversation_id)
            if conversation is None or conversation.status is not ConversationStatus.ACTIVE:
                return None
            message = message_repository.get_message(connection, message_id)
            if message is None or message.deleted_at is not None or message.conversation_id != conversation_id:
                return None
            if repository.has_pending(connection, conversation_id):
                return None
            proposal_id = new_id()
            repository.insert(
                connection,
                proposal_id=proposal_id,
                conversation_id=conversation_id,
                message_id=message_id,
                scene_json=result.model_dump_json(),
                reason=result.reason,
                now=now,
            )
            row = repository.get(connection, proposal_id)
            assert row is not None
            return repository.document(row)

        return write(self._engine, work)

    def accept(self, proposal_id: str) -> SentMessage:
        """Answers yes: queues the proposal's image as an `image_request` job and marks the proposal `accepted`, in one
        transaction. Returns the image placeholder (as `message` and `reply`) and the job.

        Raises NotFoundError for an unknown proposal, and ConflictError when it was already answered or expired, when
        its conversation is archived or deleted, and when the queue is full; then the proposal stays as it was, except
        that one older than PROPOSAL_TTL_HOURS becomes `expired`.
        """
        moment = self._clock()
        now = to_utc_text(moment)
        cutoff = to_utc_text(moment - timedelta(hours=PROPOSAL_TTL_HOURS))

        def work(connection: Connection) -> SentMessage | None:
            row = repository.get(connection, proposal_id)
            if row is None:
                raise NotFoundError(_NO_PROPOSAL)
            if row["status"] != repository.PENDING:
                raise ConflictError(_DECIDED)
            if row["created_at"] < cutoff:
                # Recorded and committed, then refused below: raising here would roll the expiry back.
                repository.decide(connection, proposal_id, repository.EXPIRED, now)
                return None
            scene: str = json.loads(row["scene_json"])["scene"]
            sent = self._images.queue_proposed(
                connection, row["conversation_id"], scene, key=f"image_proposal:{proposal_id}"
            )
            repository.decide(connection, proposal_id, repository.ACCEPTED, now, sent.job.id)
            return sent

        try:
            sent = write(self._engine, work)
        except QueueFullError:
            raise ConflictError(_QUEUE_FULL) from None
        if sent is None:
            raise ConflictError(_EXPIRED)
        return sent

    def reject(self, proposal_id: str) -> dict[str, Any]:
        """Answers no: marks the pending proposal `rejected` and returns its document. Nothing is queued.

        Raises NotFoundError for an unknown proposal and ConflictError when it was already answered or expired; one
        older than PROPOSAL_TTL_HOURS becomes `expired` instead.
        """
        moment = self._clock()
        now = to_utc_text(moment)
        cutoff = to_utc_text(moment - timedelta(hours=PROPOSAL_TTL_HOURS))

        def work(connection: Connection) -> dict[str, Any] | None:
            row = repository.get(connection, proposal_id)
            if row is None:
                raise NotFoundError(_NO_PROPOSAL)
            if row["status"] != repository.PENDING:
                raise ConflictError(_DECIDED)
            if row["created_at"] < cutoff:
                repository.decide(connection, proposal_id, repository.EXPIRED, now)
                return None
            repository.decide(connection, proposal_id, repository.REJECTED, now)
            changed = repository.get(connection, proposal_id)
            assert changed is not None
            return repository.document(changed)

        document = write(self._engine, work)
        if document is None:
            raise ConflictError(_EXPIRED)
        return document

    def expire_stale(self) -> int:
        """Expires every pending proposal older than PROPOSAL_TTL_HOURS and returns how many. Idempotent."""
        moment = self._clock()
        now = to_utc_text(moment)
        cutoff = to_utc_text(moment - timedelta(hours=PROPOSAL_TTL_HOURS))
        return write(self._engine, lambda connection: repository.expire_before(connection, cutoff, now))

    def get(self, proposal_id: str) -> dict[str, Any]:
        """Returns the proposal's document. Raises NotFoundError for an unknown proposal."""
        row = read(self._engine, lambda connection: repository.get(connection, proposal_id))
        if row is None:
            raise NotFoundError(_NO_PROPOSAL)
        return repository.document(row)
