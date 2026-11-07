"""T-ORC-09, backend side, on a migrated database file: the drain and shutdown of docs/gpu-orchestration.md (Safe
shutdown).

Step 1, the queue drain, pauses the queue through the database and waits for the running job, as `python -m
lyrveil.cli drain` does from its own process; step 2, the GPU drain, follows the orchestrator to IDLE. A shutdown that
cuts a run aborts its generation and releases its lease, and the next start queues the job again and resumes the
queue. The orchestrator side is orchestrator/tests/integration/test_drain.py.
"""

from pathlib import Path
from typing import Any

import anyio
import pytest
from anyio import to_thread
from pydantic import SecretStr
from sqlalchemy import Engine

import lyrveil.cli as cli
import lyrveil.main as backend
from integration.test_job_queue import FakeLeases, enqueue, get
from integration.test_reply_streaming import FakeRuntime, start
from lyrveil.jobs.drain import drain_queue, pause_queue, resume_queue
from lyrveil.jobs.handlers import JobDone, RunContext
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, recover_interrupted_jobs, write
from lyrveil.orchestration.client import DesiredMode, GpuStatus, OrchestratorUnavailableError
from lyrveil.orchestration.drain import drain_gpu
from lyrveil.persistence.database import create_database_engine
from lyrveil.persistence.migrations import upgrade_to_head

DEADLINE_S = 10.0


class Blocking:
    """A handler that sets `started` when its run begins, then waits for `release` and completes."""

    def __init__(self) -> None:
        self.started = anyio.Event()
        self.release = anyio.Event()

    async def __call__(self, job: Job, context: RunContext) -> JobDone:
        self.started.set()
        await self.release.wait()
        return JobDone()


class Switch:
    """A ModeSwitch that records every call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def read_state(self) -> GpuStatus:
        self.calls.append("read_state")
        return GpuStatus(state="LLM_READY", desired_mode="llm", draining=False)

    def request_mode(self, mode: DesiredMode, job_id: str | None = None) -> None:
        self.calls.append(f"request_mode {mode}")


class Control:
    """The drain calls of OrchestratorClient: `drain` answers the first of `states`, each long poll the next one,
    and the last one again once they are used up. With `down`, every call fails as an unreachable control API."""

    def __init__(self, *states: str, down: bool = False) -> None:
        self.states = list(states)
        self.down = down
        self.polls: list[tuple[int, float]] = []
        self.resumed = 0

    def _next(self) -> GpuStatus:
        if self.down:
            raise OrchestratorUnavailableError("the control API did not answer")
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return GpuStatus(state=state, desired_mode="llm", draining=True, seq=len(self.polls) + 1, boot_id="boot")

    def drain(self) -> GpuStatus:
        return self._next()

    def wait_for_state(self, since: int, wait_s: float) -> GpuStatus:
        self.polls.append((since, wait_s))
        return self._next()

    def resume(self) -> None:
        if self.down:
            raise OrchestratorUnavailableError("the control API did not answer")
        self.resumed += 1


class Ticks:
    """A monotonic clock that moves by `step` at every reading."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


@pytest.fixture
def queue() -> JobQueue:
    return JobQueue()


@pytest.fixture
def leases() -> FakeLeases:
    return FakeLeases()


@pytest.fixture
def data_root(database_path: Path) -> Path:
    """The data volume of the CLI: the database is db/lyrveil.sqlite3 under it."""
    return database_path.parents[1]


@pytest.fixture
def control(monkeypatch: pytest.MonkeyPatch) -> list[Control]:
    """Makes the CLI's control API client the next Control the test appends, and gives the CLI a token."""
    controls: list[Control] = []
    monkeypatch.setenv("LYRVEIL_CONTROL_TOKEN", "ab" * 32)

    def client(_token: SecretStr) -> Control:
        return controls.pop(0)

    monkeypatch.setattr(cli, "OrchestratorClient", client)
    return controls


def worker(engine: Engine, queue: JobQueue, leases: FakeLeases, handler: Any, **options: Any) -> JobScheduler:
    """Returns a scheduler in mode `llm` that runs `chat_reply` with `handler`."""
    handlers = {JobKind.CHAT_REPLY: handler}
    return JobScheduler(engine, queue, handlers, leases, lambda: RequiredMode.LLM, heartbeat_interval_s=0.01, **options)


async def wait_until_paused(engine: Engine, queue: JobQueue) -> None:
    while not read(engine, queue.paused):
        await anyio.sleep(0.01)


@pytest.mark.anyio
async def test_the_drain_lets_the_running_job_finish_and_starts_no_other(
    migrated: Engine, queue: JobQueue, leases: FakeLeases
) -> None:
    first = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    second = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    handler = Blocking()
    scheduler = worker(migrated, queue, leases, handler)
    drained: list[Any] = []

    async def drain() -> None:
        drained.append(await to_thread.run_sync(lambda: drain_queue(migrated, queue, DEADLINE_S, poll_s=0.01)))

    with anyio.fail_after(DEADLINE_S):
        async with anyio.create_task_group() as group:
            group.start_soon(scheduler.run_once)
            await handler.started.wait()
            group.start_soon(drain)
            await wait_until_paused(migrated, queue)
            # The running reply is not stopped: it finishes, and only then is the queue drained.
            assert get(migrated, queue, first.id).status is JobStatus.RUNNING
            handler.release.set()

    (result,) = drained
    assert (result.drained, result.running) == (True, 0)
    assert get(migrated, queue, first.id).status is JobStatus.COMPLETED
    assert await scheduler.run_once() is None
    assert get(migrated, queue, second.id).status is JobStatus.QUEUED
    # The pause is read by the statement that starts a job, so a worker that missed it still starts nothing.
    assert write(migrated, lambda connection: queue.start(connection, second.id, None, "genkey")) is None
    # A paused pass asks for no lease at all.
    assert (leases.acquired, leases.released) == ([("llm", first.id)], [("lease-1", "ok")])

    resume_queue(migrated, queue)
    resumed = await scheduler.run_once()
    assert resumed is not None
    assert (resumed.id, resumed.status) == (second.id, JobStatus.COMPLETED)


@pytest.mark.anyio
async def test_a_paused_queue_asks_the_orchestrator_for_nothing(
    migrated: Engine, queue: JobQueue, leases: FakeLeases
) -> None:
    write(migrated, lambda connection: queue.enqueue(connection, JobKind.IMAGE_REQUEST, stage="render"))
    pause_queue(migrated, queue)
    switch = Switch()
    scheduler = worker(migrated, queue, leases, Blocking(), mode_switch=switch)
    assert await scheduler.run_once() is None
    assert (switch.calls, leases.acquired) == ([], [])


@pytest.mark.anyio
async def test_a_shutdown_after_a_drain_out_of_time_hands_the_gpu_back_and_the_next_start_requeues_the_job(
    migrated: Engine, queue: JobQueue, leases: FakeLeases
) -> None:
    job = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    handler = Blocking()
    runtime = FakeRuntime()
    scheduler = worker(migrated, queue, leases, handler, text_runtime=runtime)
    with anyio.fail_after(DEADLINE_S):
        async with anyio.create_task_group() as group:
            group.start_soon(scheduler.run_once)
            await handler.started.wait()
            result = await to_thread.run_sync(lambda: drain_queue(migrated, queue, 0.05, poll_s=0.01))
            # docker compose stop: the backend's lifespan cancels the worker in the middle of the run.
            group.cancel_scope.cancel()

    assert (result.drained, result.running) == (False, 1)
    cut = get(migrated, queue, job.id)
    assert cut.status is JobStatus.RUNNING
    # The generation was aborted and the lease released, so the orchestrator's drain needs no TTL to pass.
    assert runtime.aborted == [cut.genkey]
    assert leases.released == [("lease-1", "ok")]

    (recovered,) = recover_interrupted_jobs(migrated, queue)
    assert (recovered.id, recovered.status, recovered.error_code) == (job.id, JobStatus.QUEUED, "JOB_INTERRUPTED")


def test_a_start_resumes_the_queue_the_last_stop_paused(monkeypatch: pytest.MonkeyPatch, database_path: Path) -> None:
    engine = create_database_engine(database_path)
    upgrade_to_head(engine)
    pause_queue(engine, JobQueue())
    engine.dispose()

    served = start(monkeypatch, database_path)
    assert backend.main() == 0
    (app,) = served
    restarted: Engine = app.state.engine
    assert read(restarted, JobQueue().paused) is False
    restarted.dispose()


def test_the_gpu_drain_follows_the_state_until_idle() -> None:
    control = Control("LLM_READY", "UNLOADING_LLM", "IDLE")
    assert drain_gpu(control, 90).state == "IDLE"
    assert control.polls == [(1, 25.0), (2, 25.0)]

    stuck = Control("GENERATING_TEXT")
    assert drain_gpu(stuck, 30, monotonic=Ticks(10.0)).state == "GENERATING_TEXT"
    # Each long poll waits no longer than the time left.
    assert [wait for _, wait in stuck.polls] == [20.0, 10.0]


def test_the_drain_command_pauses_the_queue_and_reports_a_job_still_running(
    migrated: Engine, queue: JobQueue, data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.run(["drain", "--timeout", "0"], data_root) == 0
    assert "PASS     Queue drain: the job queue is paused and no job is running" in capsys.readouterr().out
    assert read(migrated, queue.paused) is True

    resume_queue(migrated, queue)
    job = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    write(migrated, lambda connection: queue.start(connection, job.id, None, "genkey"))
    assert cli.run(["drain", "--timeout", "0"], data_root) == 1
    assert "FAILURE  Queue drain: 1 job(s) still running after 0 s" in capsys.readouterr().err
    assert cli.run(["drain", "--timeout", "-1"], data_root) == 1
    assert cli.run(["drain"], data_root / "elsewhere") == 1
    assert not (data_root / "elsewhere" / "db").exists()


def test_the_gpu_drain_and_resume_commands_report_each_outcome(
    migrated: Engine, queue: JobQueue, data_root: Path, control: list[Control], capsys: pytest.CaptureFixture[str]
) -> None:
    control.append(Control("UNLOADING_LLM", "IDLE"))
    assert cli.run(["drain-gpu"], data_root) == 0
    assert "PASS     Runtime release: the runtime is stopped" in capsys.readouterr().out
    control.append(Control("ERROR"))
    assert cli.run(["drain-gpu"], data_root) == 1
    assert "the orchestrator is in ERROR" in capsys.readouterr().err
    control.append(Control("IDLE", down=True))
    assert cli.run(["drain-gpu", "--timeout", "5"], data_root) == 1
    assert "the orchestrator did not answer" in capsys.readouterr().err

    pause_queue(migrated, queue)
    resumed = Control("IDLE")
    control.append(resumed)
    assert cli.run(["resume"], data_root) == 0
    assert read(migrated, queue.paused) is False
    assert resumed.resumed == 1
    pause_queue(migrated, queue)
    control.append(Control("IDLE", down=True))
    assert cli.run(["resume"], data_root) == 0
    assert "WARNING  GPU: the orchestrator did not answer" in capsys.readouterr().out
    assert read(migrated, queue.paused) is False
