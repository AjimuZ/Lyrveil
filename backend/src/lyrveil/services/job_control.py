"""JobControlService: the user's cancellation of a job (docs/gpu-orchestration.md, Cancellation), and
`remove_placeholder`, the removal of what a cancelled job showed, which the reply and image handlers share.

A queued job is cancelled at once, in the same transaction as the removal of its placeholder. A running job in a stage
that needs the GPU gets a cancellation request: the scheduler stops it, aborts the runtime's work and ends it
`cancelled` (lyrveil.jobs.scheduler). A running stage that needs no GPU, such as an image's `persist`, is about to
finish and is not cancelled. No message text is ever logged.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.domain.errors import ConflictError, NotFoundError
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.domain.messages import MessageStatus, VariantStatus, message_document
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import write
from lyrveil.logs import get_logger
from lyrveil.media.store import MediaStore
from lyrveil.persistence import messages as repository
from lyrveil.ports.events import MESSAGE_DELETED, MESSAGE_UPDATED
from lyrveil.services.invalidation import InvalidationService

_NO_JOB = "Operazione non trovata."
_ENDED = "L'operazione è già terminata."
_FINISHING = "L'operazione sta finendo e non si può più annullare."
# The kinds whose message is a placeholder that disappears with a cancelled job.
_REMOVED_MESSAGE_KINDS = frozenset({JobKind.CHAT_REPLY, JobKind.IMAGE_REQUEST})

# One event to publish after the commit: its type and its data.
Announcement = tuple[str, dict[str, Any]]

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def remove_placeholder(
    connection: Connection, job: Job, now: datetime, invalidation: InvalidationService | None = None
) -> list[Announcement]:
    """Removes the placeholder that the cancelled `job` showed, in the caller's write transaction, and returns the
    events that announce it, to publish after the commit.

    Acts only while the job's message is still `queued`, so a reply that the cancellation interrupted, one that is
    already finished and a deleted message are left as they are. The reply of a `chat_reply` and the image of an
    `image_request` are soft-deleted (`message.deleted`): an empty placeholder supports no memory or summary, so nothing
    is invalidated. The new variant of a `chat_regenerate` is marked `failed`, and the message shows again the newest
    variant before it (`message.updated`), with `invalidation` applied to that change when given. Never commits.
    """
    if job.message_id is None:
        return []
    message = repository.get_message(connection, job.message_id)
    if message is None or message.deleted_at is not None or message.status is not MessageStatus.QUEUED:
        return []
    if job.kind in _REMOVED_MESSAGE_KINDS:
        repository.update_message(connection, message.id, deleted_at=to_utc_text(now))
        return [(MESSAGE_DELETED, {"message_id": message.id})]
    if job.kind is not JobKind.CHAT_REGENERATE:
        return []
    cancelled_variant = job.payload.get("variant_id")
    earlier = [
        variant for variant in repository.list_variants(connection, message.id) if variant.id != cancelled_variant
    ]
    if isinstance(cancelled_variant, str):
        repository.update_variant(connection, cancelled_variant, status=VariantStatus.FAILED.value)
    if not earlier:
        repository.update_message(connection, message.id, status=MessageStatus.FAILED.value)
    else:
        shown = earlier[-1]
        repository.update_message(
            connection,
            message.id,
            content=shown.content,
            active_variant_id=shown.id,
            status=MessageStatus(shown.status.value).value,
        )
    restored = repository.get_message(connection, message.id)
    assert restored is not None
    if invalidation is not None:
        invalidation.message_changed(connection, restored, now)
    return [(MESSAGE_UPDATED, {"message": message_document(restored)})]


@dataclass(frozen=True)
class Cancellation:
    """The result of a cancellation: the job after it (`cancelled`, or still `running` with `cancel_requested_at`
    set), and the events to publish now that it is committed, in order."""

    job: Job
    events: tuple[Announcement, ...] = ()


class JobControlService:
    """Cancels jobs of `engine`. `media` holds the staged file of an image, removed with its cancelled job. `clock`
    returns the aware current time and `zone` is the deployment time zone, which the invalidation of a restored
    regeneration needs."""

    def __init__(
        self,
        engine: Engine,
        media: MediaStore | None = None,
        queue: JobQueue | None = None,
        clock: Callable[[], datetime] = _utc_now,
        zone: tzinfo = UTC,
    ) -> None:
        self._engine = engine
        self._media = media
        self._clock = clock
        self._queue = queue or JobQueue(clock=clock)
        self._invalidation = InvalidationService(self._queue, zone)

    def cancel(self, job_id: str) -> Cancellation:
        """Cancels the job, in one transaction, and returns what changed.

        A queued job ends `cancelled` at once with JOB_CANCELLED and its placeholder is removed (remove_placeholder);
        an image's staged file is deleted after the commit. A running job whose stage needs the GPU keeps running with
        its cancellation recorded: the scheduler, which the caller must wake, stops it. A repeated request for it
        changes nothing. Raises NotFoundError for an unknown job, and ConflictError for a job that already ended or
        runs a stage without a GPU, which finishes on its own in a moment.
        """
        moment = self._clock()

        def work(connection: Connection) -> Cancellation:
            job = self._queue.get(connection, job_id)
            if job is None:
                raise NotFoundError(_NO_JOB)
            if job.status is JobStatus.QUEUED:
                cancelled = self._queue.cancel_queued(connection, job_id)
                assert cancelled is not None
                removed = remove_placeholder(connection, cancelled, moment, self._invalidation)
                return Cancellation(cancelled, tuple(removed))
            if job.status is JobStatus.RUNNING:
                if job.required_mode is RequiredMode.NONE:
                    raise ConflictError(_FINISHING)
                requested = self._queue.request_cancel(connection, job_id)
                assert requested is not None
                return Cancellation(requested)
            raise ConflictError(_ENDED)

        result = write(self._engine, work)
        cancelled_image = result.job.status is JobStatus.CANCELLED and result.job.kind is JobKind.IMAGE_REQUEST
        if cancelled_image and self._media is not None:
            try:
                self._media.discard_staged(result.job.id)
            except OSError:
                # Only wasted space: no stage reads the file of a cancelled job.
                logger.warning("job_staged_file_not_removed", job_id=result.job.id)
        logger.info("job_cancel_requested", job_id=result.job.id, state=result.job.status.value)
        return result
