"""LifecycleEventRecorder: stores the orchestrator's events in `model_lifecycle_events` (docs/gpu-orchestration.md,
Monitoring and logs).

The orchestrator keeps its events in memory only, the newest 5000 of its process (GET /v1/events), so the backend,
the single writer of the database (ADR 0004), copies them. Reading resumes after the newest stored event, so a backend
restart neither loses the events the orchestrator still buffers nor stores any of them twice: `(boot_id, seq)` is
unique, and an event already stored is skipped. Events the orchestrator dropped before the backend read them, such as
those of an orchestrator process that ended meanwhile, cannot be recovered; the gap is logged with its size.
"""

import json
from collections.abc import Callable
from typing import Protocol

from sqlalchemy import Connection, Engine

from lyrveil.domain.identifiers import to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.orchestration.client import EventPage, GpuStatus, LifecycleEvent, OrchestratorUnavailableError
from lyrveil.persistence import lifecycle_events as repository
from lyrveil.persistence.lifecycle_events import KEEP_EVENTS, EventRow

# The `details` key of a load or a release that reports the memory in use once it ended (docs/api.md, GET /v1/events).
_USED_AFTER = "used_after_mib"

logger = get_logger(__name__)


class EventSource(Protocol):
    """GET /v1/events of OrchestratorClient. It blocks, so callers run the recorder in a worker thread."""

    def read_events(self, boot_id: str | None, after: int) -> EventPage: ...


def event_row(boot_id: str, event: LifecycleEvent) -> EventRow:
    """Returns the row that stores `event` of the orchestrator process `boot_id`. `vram_used_mib` is the memory in use
    the event reports after its load or release, None for other events; `vram_total_mib` is None, since no event
    carries the card's total."""
    used = event.details.get(_USED_AFTER)
    return EventRow(
        boot_id=boot_id,
        seq=event.seq,
        from_state=event.source,
        to_state=event.target,
        reason=event.reason,
        code=event.code,
        job_id=event.job_id,
        vram_total_mib=None,
        vram_used_mib=used,
        detail_json=json.dumps(dict(event.details), separators=(",", ":"), sort_keys=True),
        occurred_at=to_utc_text(event.at),
    )


class LifecycleEventRecorder:
    """Copies the events of `source` into `engine`'s `model_lifecycle_events`, keeping the newest `keep` rows.

    `record` and `observe` block: run them in a worker thread. GpuStateFollower calls `observe` for each new GPU state,
    and every new state comes with at least one new event, so the copy follows the orchestrator closely.
    """

    def __init__(self, engine: Engine, source: EventSource, keep: int = KEEP_EVENTS) -> None:
        if keep < 1:
            raise ValueError("keep must be at least 1")
        self._engine = engine
        self._source = source
        self._keep = keep

    def record(self) -> int:
        """Reads the events after the newest stored one and stores them in one transaction, then keeps the newest
        `keep` rows. Returns how many events it stored.

        Reading resumes with the stored cursor, so a new recorder over the same database, as after a backend restart,
        continues where the last one stopped. For another orchestrator process the whole buffer of the new process is
        read. Raises OrchestratorUnavailableError, storing nothing, when the control API does not answer.
        """
        cursor = read(self._engine, repository.cursor)
        boot_id, after = (None, 0) if cursor is None else (cursor.boot_id, cursor.seq)
        page = self._source.read_events(boot_id, after)
        floor = after if page.boot_id == boot_id else 0
        new = [event for event in page.events if event.seq > floor]
        if not new:
            return 0
        missing = new[0].seq - floor - 1
        if missing > 0:
            # The orchestrator's buffer no longer holds them, or they belong to a process that ended meanwhile.
            logger.warning("lifecycle_events_gap", missing_count=missing)

        def store(connection: Connection) -> int:
            stored = sum(repository.insert(connection, event_row(page.boot_id, event)) for event in new)
            repository.prune(connection, self._keep)
            return stored

        return write(self._engine, store)

    def observe(self, _status: GpuStatus) -> None:
        """Records the events behind a new GPU state, as GpuStateFollower's `on_state`. An orchestrator that does not
        answer is logged and retried with the next state; nothing read so far is lost."""
        try:
            self.record()
        except OrchestratorUnavailableError:
            logger.info("lifecycle_events_unavailable")


def each(*observers: Callable[[GpuStatus], object]) -> Callable[[GpuStatus], None]:
    """Returns one `on_state` observer that passes each state to every one of `observers`, in order. An exception of
    one is logged and the others still run, so a failed launch-level check never stops the event copy."""

    def observe(status: GpuStatus) -> None:
        for observer in observers:
            try:
                observer(status)
            except Exception:
                logger.error("gpu_state_observer_failed", exc_info=True)

    return observe
