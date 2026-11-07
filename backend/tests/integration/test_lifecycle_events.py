"""T-ORC-11 on a migrated database file: the orchestrator's events replay without duplicates after a backend restart.

The orchestrator is a fake event buffer with the semantics of GET /v1/events: the events after `after` for its own
`boot_id`, every buffered event for another. The recorder, the job worker's GPU state follower and the database are
the real ones; a backend restart is a new engine, recorder and application on the same file.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from sqlalchemy import Engine, text

from integration.test_reply_streaming import FakeRuntime, GpuStates, Leases, opened, running
from lyrveil.main import Worker, create_app
from lyrveil.orchestration.client import EventPage, GpuStatus, LifecycleEvent, OrchestratorUnavailableError
from lyrveil.orchestration.lifecycle_events import LifecycleEventRecorder
from lyrveil.persistence.database import create_database_engine
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

START = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
# The moves of a start from mode `llm`, then a lease, as the orchestrator records them.
MOVES = (
    ("RECOVERING", "IDLE", "nothing running"),
    ("IDLE", "LOADING_LLM", "mode llm"),
    ("LOADING_LLM", "LLM_READY", "healthy, model verified"),
    ("LLM_READY", "GENERATING_TEXT", "lease granted"),
    ("GENERATING_TEXT", "LLM_READY", "lease released"),
)


class FakeOrchestrator:
    """GET /v1/events of one orchestrator process at a time, keeping its newest `buffer` events. `restart` starts a
    new process, with a new `boot_id` and `seq` from 1. Every read is recorded; while `down`, reads fail."""

    def __init__(self, buffer: int = 5000) -> None:
        self.buffer = buffer
        self.boot_id = "boot-1"
        self.events: list[LifecycleEvent] = []
        self.reads: list[tuple[str | None, int]] = []
        self.down = False
        self.replay_all = False
        self._boots = 1
        self._ticks = 0

    def emit(self, *moves: tuple[str, str, str], job_id: str | None = None) -> None:
        for source, target, reason in moves:
            self._ticks += 1
            details = {"used_after_mib": 9000 + self._ticks} if target == "LLM_READY" else {}
            self.events.append(
                LifecycleEvent(
                    seq=len(self.events) + 1,
                    at=START + timedelta(seconds=self._ticks),
                    source=source,
                    target=target,
                    reason=reason,
                    job_id=job_id,
                    code=None,
                    details=details,
                )
            )

    def restart(self) -> None:
        self._boots += 1
        self.boot_id = f"boot-{self._boots}"
        self.events = []

    def read_events(self, boot_id: str | None, after: int) -> EventPage:
        self.reads.append((boot_id, after))
        if self.down:
            raise OrchestratorUnavailableError("the control API did not answer GET /v1/events")
        floor = 0 if self.replay_all or boot_id != self.boot_id else after
        kept = self.events[-self.buffer :]
        return EventPage(self.boot_id, tuple(event for event in kept if event.seq > floor))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


@pytest.fixture
def restarted() -> Iterator[list[Engine]]:
    """Collects the engines a test opens on the same file for a restart, and disposes them afterwards."""
    engines: list[Engine] = []
    yield engines
    for engine in engines:
        engine.dispose()


def rows(engine: Engine) -> list[tuple[Any, ...]]:
    """Returns (boot_id, seq, from, to, reason, job_id, vram_used_mib, detail_json, occurred_at) of each stored event,
    in the order of the orchestrator."""
    with engine.connect() as connection:
        found = connection.execute(
            text(
                "SELECT boot_id, seq, from_state, to_state, reason, job_id, vram_used_mib, detail_json, occurred_at "
                "FROM model_lifecycle_events ORDER BY occurred_at, seq"
            )
        ).all()
    return [tuple(row) for row in found]


def stored(engine: Engine) -> list[tuple[str, int]]:
    return [(row[0], row[1]) for row in rows(engine)]


def test_events_replay_without_duplicates_after_a_backend_restart(
    migrated: Engine, database_path: Path, restarted: list[Engine]
) -> None:
    orchestrator = FakeOrchestrator()
    orchestrator.emit(*MOVES[:3])
    assert LifecycleEventRecorder(migrated, orchestrator).record() == 3
    migrated.dispose()

    # The backend restarts while the orchestrator goes on: the new process resumes after the last stored event.
    orchestrator.emit(*MOVES[3:], job_id="job-1")
    engine = create_database_engine(database_path)
    restarted.append(engine)
    recorder = LifecycleEventRecorder(engine, orchestrator)
    assert recorder.record() == 2
    assert orchestrator.reads == [(None, 0), ("boot-1", 3)]
    assert recorder.record() == 0

    # An answer that overlaps what is stored, as after a read that was cut, stores nothing twice.
    orchestrator.replay_all = True
    assert recorder.record() == 0
    assert stored(engine) == [("boot-1", seq) for seq in range(1, 6)]

    first, ready, granted = rows(engine)[0], rows(engine)[2], rows(engine)[3]
    assert first == ("boot-1", 1, "RECOVERING", "IDLE", "nothing running", None, None, "{}", "2026-11-07T10:00:01.000Z")
    assert ready[2:8] == ("LOADING_LLM", "LLM_READY", "healthy, model verified", None, 9003, '{"used_after_mib":9003}')
    assert granted[2:6] == ("LLM_READY", "GENERATING_TEXT", "lease granted", "job-1")


def test_a_new_orchestrator_process_is_read_from_its_first_event_and_the_old_rows_stay(migrated: Engine) -> None:
    orchestrator = FakeOrchestrator()
    recorder = LifecycleEventRecorder(migrated, orchestrator)
    orchestrator.emit(*MOVES)
    assert recorder.record() == 5

    orchestrator.restart()
    orchestrator.emit(*MOVES[:3])
    # The stored cursor names the old process, so the new one answers with its whole buffer.
    assert recorder.record() == 3
    assert orchestrator.reads[-1] == ("boot-1", 5)
    orchestrator.emit(MOVES[3])
    assert recorder.record() == 1
    assert orchestrator.reads[-1] == ("boot-2", 3)
    assert stored(migrated) == [*(("boot-1", seq) for seq in range(1, 6)), *(("boot-2", seq) for seq in range(1, 5))]


def test_events_the_orchestrator_no_longer_buffers_are_skipped_and_the_rest_stored(migrated: Engine) -> None:
    orchestrator = FakeOrchestrator(buffer=2)
    orchestrator.emit(*MOVES)
    assert LifecycleEventRecorder(migrated, orchestrator).record() == 2
    assert stored(migrated) == [("boot-1", 4), ("boot-1", 5)]


def test_an_orchestrator_that_does_not_answer_loses_nothing(migrated: Engine) -> None:
    orchestrator = FakeOrchestrator()
    recorder = LifecycleEventRecorder(migrated, orchestrator)
    orchestrator.emit(*MOVES[:2])
    orchestrator.down = True
    status = GpuStatus(state="LOADING_LLM", desired_mode="llm", draining=False, seq=2, boot_id="boot-1")
    recorder.observe(status)
    assert stored(migrated) == []
    orchestrator.down = False
    recorder.observe(status)
    assert stored(migrated) == [("boot-1", 1), ("boot-1", 2)]


def test_only_the_newest_rows_are_kept(migrated: Engine) -> None:
    orchestrator = FakeOrchestrator()
    recorder = LifecycleEventRecorder(migrated, orchestrator, keep=3)
    orchestrator.emit(*MOVES)
    assert recorder.record() == 5
    assert stored(migrated) == [("boot-1", 3), ("boot-1", 4), ("boot-1", 5)]
    orchestrator.emit(MOVES[3])
    assert recorder.record() == 1
    assert stored(migrated) == [("boot-1", 4), ("boot-1", 5), ("boot-1", 6)]


async def wait_for_rows(engine: Engine, count: int) -> None:
    with anyio.fail_after(10):
        while len(rows(engine)) < count:
            await anyio.sleep(0.02)


def worker_for(orchestrator: FakeOrchestrator, *seqs: int) -> Worker:
    """Returns a worker whose GPU state follower reads the states of `seqs` in turn, and whose events come from
    `orchestrator`."""
    states = (
        GpuStatus(state="LLM_READY", desired_mode="llm", draining=False, seq=seq, boot_id="boot-1") for seq in seqs
    )
    return Worker(
        text_runtime=lambda: opened(FakeRuntime()),
        leases=Leases(),
        gpu_states=GpuStates(*states),
        lifecycle_events=orchestrator,
    )


@pytest.mark.anyio
async def test_the_job_worker_stores_the_events_and_a_restarted_backend_adds_only_the_new_ones(
    settings: Settings, migrated: Engine, database_path: Path, restarted: list[Engine]
) -> None:
    orchestrator = FakeOrchestrator()
    orchestrator.emit(*MOVES[:3])
    app = create_app(settings, migrated, worker=worker_for(orchestrator, 3))
    async with running(app):
        await wait_for_rows(migrated, 3)
    migrated.dispose()

    orchestrator.emit(*MOVES[3:], job_id="job-1")
    engine = create_database_engine(database_path)
    restarted.append(engine)
    again = create_app(settings, engine, worker=worker_for(orchestrator, 5, 5))
    async with running(again):
        await wait_for_rows(engine, 5)
        await anyio.sleep(0.1)
    assert stored(engine) == [("boot-1", seq) for seq in range(1, 6)]
    assert ("boot-1", 3) in orchestrator.reads
