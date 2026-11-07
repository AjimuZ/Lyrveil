"""Tests of lyrveil/jobs on a real SQLite file: durability with the creating change, restart recovery, retries with
backoff, priorities, mode eligibility, and the scheduler's lease handling."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from sqlalchemy import Connection, Engine, text

from lyrveil.jobs.handlers import (
    JobDone,
    JobFailedError,
    JobHandler,
    JobOutcome,
    JobPreemptedError,
    NextStage,
    RunContext,
)
from lyrveil.jobs.policy import POLICIES, JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import JOB_INTERRUPTED, Job, JobQueue, QueueFullError
from lyrveil.jobs.scheduler import JobScheduler, read, recover_interrupted_jobs, write
from lyrveil.orchestration.client import Lease, LeaseLostError, LeaseMode, LeaseOutcome, LeaseRefusedError
from lyrveil.persistence.database import create_database_engine
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import RuntimeOutOfMemoryError

START = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
LLM_ONLY = {RequiredMode.LLM, RequiredMode.NONE}
ALL_KINDS = tuple(JobKind)


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeLeases:
    """Grants every lease unless `refuse` is set; `lose` makes every heartbeat report the lease lost."""

    def __init__(self) -> None:
        self.refuse = False
        self.lose = False
        self.acquired: list[tuple[LeaseMode, str]] = []
        self.released: list[tuple[str, LeaseOutcome]] = []

    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease:
        if self.refuse:
            raise LeaseRefusedError("NOT_READY")
        self.acquired.append((mode, job_id))
        return Lease(id=f"lease-{len(self.acquired)}", mode=mode, job_id=job_id, ttl_s=30, expires_in_s=30.0)

    def renew_lease(self, lease: Lease) -> Lease:
        if self.lose:
            raise LeaseLostError(lease.id)
        return lease

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        self.released.append((lease.id, outcome))
        return True


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def queue(clock: FakeClock) -> JobQueue:
    return JobQueue(clock=clock)


@pytest.fixture
def leases() -> FakeLeases:
    return FakeLeases()


def enqueue(engine: Engine, queue: JobQueue, kind: JobKind, idempotency_key: str | None = None) -> Job:
    return write(engine, lambda connection: queue.enqueue(connection, kind, idempotency_key=idempotency_key))


def get(engine: Engine, queue: JobQueue, job_id: str) -> Job:
    job = read(engine, lambda connection: queue.get(connection, job_id))
    assert job is not None
    return job


def start(engine: Engine, queue: JobQueue, job_id: str) -> Job:
    started = write(engine, lambda connection: queue.start(connection, job_id, None, "genkey"))
    assert started is not None
    return started


def fail(engine: Engine, queue: JobQueue, job_id: str, *, retryable: bool = True) -> Job:
    code, detail = "RUNTIME_UNAVAILABLE", "no answer"
    return write(engine, lambda connection: queue.fail(connection, job_id, code, detail, retryable=retryable))


def eligible(engine: Engine, queue: JobQueue, modes: set[RequiredMode] = LLM_ONLY) -> Job | None:
    return read(engine, lambda connection: queue.next_eligible(connection, modes, ALL_KINDS))


def test_a_job_is_committed_with_the_change_that_creates_it_and_survives_a_restart(
    migrated: Engine, queue: JobQueue, database_path: Path
) -> None:
    def change(connection: Connection, fail_after: bool) -> str:
        connection.execute(text("INSERT INTO application_settings VALUES ('sample', '{}', '2026-11-07T10:00:00.000Z')"))
        job = queue.enqueue(connection, JobKind.CHAT_REPLY, {"message": "m1"}, idempotency_key="send-1")
        if fail_after:
            raise RuntimeError("the change fails after the job was written")
        return job.id

    with pytest.raises(RuntimeError):
        write(migrated, lambda connection: change(connection, fail_after=True))
    assert eligible(migrated, queue) is None
    job_id = write(migrated, lambda connection: change(connection, fail_after=False))
    migrated.dispose()

    restarted = create_database_engine(database_path)
    try:
        job = get(restarted, queue, job_id)
        assert (job.status, job.stage, job.required_mode, job.attempt) == (JobStatus.QUEUED, "run", RequiredMode.LLM, 0)
        assert job.payload == {"message": "m1"}
        assert (job.priority, job.max_attempts) == (10, 2)
        again = enqueue(restarted, queue, JobKind.CHAT_REPLY, idempotency_key="send-1")
        assert again.id == job_id
    finally:
        restarted.dispose()


def test_every_kind_can_be_stored_with_its_documented_policy(migrated: Engine, queue: JobQueue) -> None:
    for kind in JobKind:
        job = enqueue(migrated, queue, kind)
        policy = POLICIES[kind]
        assert (job.priority, job.max_attempts, job.stage) == (policy.priority, policy.max_attempts, policy.first_stage)
    image = enqueue(migrated, queue, JobKind.IMAGE_REQUEST)
    assert (image.stage, image.required_mode) == ("prompt", RequiredMode.LLM)
    maintenance = enqueue(migrated, queue, JobKind.MAINTENANCE)
    assert maintenance.required_mode is RequiredMode.NONE


def test_restart_requeues_an_interrupted_job_and_fails_one_on_its_last_attempt(
    migrated: Engine, queue: JobQueue
) -> None:
    reply = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    proposal = enqueue(migrated, queue, JobKind.IMAGE_PROPOSAL)
    finished = enqueue(migrated, queue, JobKind.MAINTENANCE)
    for job in (reply, proposal, finished):
        start(migrated, queue, job.id)
    write(migrated, lambda connection: queue.complete(connection, finished.id, {"removed": 0}))

    recovered = {job.id: job for job in recover_interrupted_jobs(migrated, queue)}

    assert set(recovered) == {reply.id, proposal.id}
    requeued = recovered[reply.id]
    assert (requeued.status, requeued.attempt, requeued.lease_id) == (JobStatus.QUEUED, 1, None)
    assert requeued.error_code == JOB_INTERRUPTED
    ended = recovered[proposal.id]
    assert (ended.status, ended.error_code) == (JobStatus.FAILED, JOB_INTERRUPTED)
    assert ended.finished_at is not None
    assert get(migrated, queue, finished.id).status is JobStatus.COMPLETED
    assert recover_interrupted_jobs(migrated, queue) == []


def test_a_reply_retries_once_after_five_seconds_then_fails_with_its_reason(
    migrated: Engine, queue: JobQueue, clock: FakeClock
) -> None:
    job = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    start(migrated, queue, job.id)
    retried = fail(migrated, queue, job.id)
    assert (retried.status, retried.error_code, retried.not_before) == (
        JobStatus.QUEUED,
        "RUNTIME_UNAVAILABLE",
        START + timedelta(seconds=5),
    )
    clock.advance(4.9)
    assert eligible(migrated, queue) is None
    clock.advance(0.1)
    assert eligible(migrated, queue) == retried
    assert start(migrated, queue, job.id).attempt == 2
    ended = fail(migrated, queue, job.id)
    assert (ended.status, ended.error_code) == (JobStatus.FAILED, "RUNTIME_UNAVAILABLE")
    assert ended.error_detail == "no answer"
    assert ended.finished_at == clock.now


def test_extraction_backs_off_one_then_five_minutes_and_stops_after_three_attempts(
    migrated: Engine, queue: JobQueue, clock: FakeClock
) -> None:
    job = enqueue(migrated, queue, JobKind.MEMORY_EXTRACTION)
    waits = []
    for _ in range(2):
        start(migrated, queue, job.id)
        retried = fail(migrated, queue, job.id)
        assert retried.not_before is not None
        waits.append((retried.not_before - clock.now).total_seconds())
        clock.now = retried.not_before
    assert waits == [60.0, 300.0]
    start(migrated, queue, job.id)
    assert fail(migrated, queue, job.id).status is JobStatus.FAILED


def test_a_failure_that_is_not_retryable_ends_the_job_at_once(migrated: Engine, queue: JobQueue) -> None:
    job = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    start(migrated, queue, job.id)
    assert fail(migrated, queue, job.id, retryable=False).status is JobStatus.FAILED


def test_selection_follows_priority_then_age_and_skips_jobs_of_another_mode(
    migrated: Engine, queue: JobQueue, clock: FakeClock
) -> None:
    maintenance = enqueue(migrated, queue, JobKind.MAINTENANCE)
    clock.advance(1)
    older_reply = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    clock.advance(1)
    enqueue(migrated, queue, JobKind.CHAT_REPLY)
    assert eligible(migrated, queue) == older_reply
    assert eligible(migrated, queue, {RequiredMode.IMAGE, RequiredMode.NONE}) == maintenance
    assert eligible(migrated, queue, {RequiredMode.IMAGE}) is None


def test_the_queue_refuses_a_job_past_its_cap(migrated: Engine, clock: FakeClock) -> None:
    small = JobQueue(clock=clock, max_active=2)
    enqueue(migrated, small, JobKind.CHAT_REPLY)
    enqueue(migrated, small, JobKind.CHAT_REPLY)
    with pytest.raises(QueueFullError):
        enqueue(migrated, small, JobKind.CHAT_REPLY)


def scheduler(
    engine: Engine,
    queue: JobQueue,
    leases: FakeLeases,
    handler: JobHandler,
    mode: RequiredMode = RequiredMode.LLM,
    kinds: tuple[JobKind, ...] = (JobKind.CHAT_REPLY, JobKind.IMAGE_REQUEST, JobKind.MAINTENANCE),
) -> JobScheduler:
    return JobScheduler(engine, queue, dict.fromkeys(kinds, handler), leases, lambda: mode, heartbeat_interval_s=0.01)


class Recorder:
    """A handler that records its calls and answers with `outcome`, or raises it."""

    def __init__(self, outcome: JobOutcome | Exception) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, RunContext]] = []

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        self.calls.append((job.id, context))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.anyio
async def test_the_scheduler_runs_a_job_under_a_lease_and_stores_its_result(
    migrated: Engine, queue: JobQueue, leases: FakeLeases
) -> None:
    job = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    handler = Recorder(JobDone({"message_id": "m2"}))
    finished = await scheduler(migrated, queue, leases, handler).run_once()
    assert finished is not None
    assert (finished.status, finished.result, finished.attempt) == (JobStatus.COMPLETED, {"message_id": "m2"}, 1)
    ((called_id, context),) = handler.calls
    assert called_id == job.id
    assert context.lease is not None
    assert context.genkey == finished.genkey
    assert leases.acquired == [("llm", job.id)]
    assert leases.released == [(context.lease.id, "ok")]


@pytest.mark.anyio
async def test_a_job_of_another_mode_waits_instead_of_running(
    migrated: Engine, queue: JobQueue, leases: FakeLeases
) -> None:
    job = enqueue(migrated, queue, JobKind.CHAT_REPLY)
    handler = Recorder(JobDone())
    assert await scheduler(migrated, queue, leases, handler, mode=RequiredMode.IMAGE).run_once() is None
    assert await scheduler(migrated, queue, leases, handler, kinds=(JobKind.MAINTENANCE,)).run_once() is None
    leases.refuse = True
    assert await scheduler(migrated, queue, leases, handler).run_once() is None
    assert handler.calls == []
    assert get(migrated, queue, job.id).status is JobStatus.QUEUED


@pytest.mark.anyio
async def test_a_stage_without_a_mode_runs_with_no_lease(migrated: Engine, queue: JobQueue, leases: FakeLeases) -> None:
    enqueue(migrated, queue, JobKind.MAINTENANCE)
    handler = Recorder(JobDone())
    finished = await scheduler(migrated, queue, leases, handler, mode=RequiredMode.NONE).run_once()
    assert finished is not None
    assert finished.status is JobStatus.COMPLETED
    assert handler.calls[0][1].lease is None
    assert leases.acquired == []


@pytest.mark.anyio
async def test_a_runtime_failure_is_retried_and_releases_the_lease_as_oom(
    migrated: Engine, queue: JobQueue, leases: FakeLeases
) -> None:
    enqueue(migrated, queue, JobKind.CHAT_REPLY)
    finished = await scheduler(migrated, queue, leases, Recorder(RuntimeOutOfMemoryError())).run_once()
    assert finished is not None
    assert (finished.status, finished.error_code, finished.attempt) == (JobStatus.QUEUED, "LLM_OOM", 1)
    assert leases.released == [("lease-1", "oom")]


@pytest.mark.anyio
async def test_a_handler_bug_ends_the_job_and_a_job_failure_keeps_its_code(
    migrated: Engine, queue: JobQueue, leases: FakeLeases
) -> None:
    enqueue(migrated, queue, JobKind.CHAT_REPLY)
    crashed = await scheduler(migrated, queue, leases, Recorder(KeyError("x"))).run_once()
    assert crashed is not None
    assert (crashed.status, crashed.error_code) == (JobStatus.FAILED, "INTERNAL")
    enqueue(migrated, queue, JobKind.CHAT_REPLY)
    refused = Recorder(JobFailedError("STRUCTURED_OUTPUT_INVALID", retryable=False))
    ended = await scheduler(migrated, queue, leases, refused).run_once()
    assert ended is not None
    assert (ended.status, ended.error_code) == (JobStatus.FAILED, "STRUCTURED_OUTPUT_INVALID")


@pytest.mark.anyio
async def test_a_new_stage_changes_the_mode_and_preemption_costs_no_attempt(
    migrated: Engine, queue: JobQueue, leases: FakeLeases
) -> None:
    job = enqueue(migrated, queue, JobKind.IMAGE_REQUEST)
    advanced = await scheduler(migrated, queue, leases, Recorder(NextStage("render", {"seed": 7}))).run_once()
    assert advanced is not None
    assert (advanced.status, advanced.stage, advanced.required_mode) == (JobStatus.QUEUED, "render", RequiredMode.IMAGE)
    assert (advanced.attempt, advanced.payload) == (0, {"seed": 7})
    assert await scheduler(migrated, queue, leases, Recorder(JobDone())).run_once() is None
    preempting = scheduler(migrated, queue, leases, Recorder(JobPreemptedError()), mode=RequiredMode.IMAGE)
    preempted = await preempting.run_once()
    assert preempted is not None
    assert (preempted.id, preempted.status, preempted.attempt) == (job.id, JobStatus.QUEUED, 0)
    assert leases.acquired[-1] == ("image", job.id)


@pytest.mark.anyio
async def test_a_lost_lease_stops_the_handler_and_retries_with_lease_lost(
    migrated: Engine, queue: JobQueue, leases: FakeLeases
) -> None:
    enqueue(migrated, queue, JobKind.CHAT_REPLY)
    leases.lose = True
    reached_the_end: list[bool] = []

    async def slow(job: Job, context: RunContext) -> JobDone:
        await anyio.sleep(5)
        reached_the_end.append(True)
        return JobDone()

    with anyio.fail_after(3):
        finished = await scheduler(migrated, queue, leases, slow).run_once()
    assert finished is not None
    assert (finished.status, finished.error_code) == (JobStatus.QUEUED, "LEASE_LOST")
    assert reached_the_end == []
    assert leases.released == []
