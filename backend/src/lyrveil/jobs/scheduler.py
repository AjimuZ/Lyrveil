"""JobScheduler: the single worker that picks an eligible job, holds its lease, runs its handler and stores the outcome.

The base rule: a job runs when its stage needs no GPU or needs the mode the GPU is in, and a handler for its kind is
registered; anything else waits in the queue. With a ModeSwitch (P05.7), the scheduler also asks the orchestrator for
the mode its queue needs, by the reduced rule of `mode_deciding_job`. Since P06.2 it ends the job that waits for that
mode once the orchestrator has held the load back for VRAM during `vram_wait_s`. The image wait limit,
`images.max_in_a_row`, batching, fairness and preemption across modes come with P06.3 (docs/gpu-orchestration.md,
Selection).
"""

import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import anyio
from anyio import to_thread
from sqlalchemy import Connection, Engine

from lyrveil.domain.identifiers import new_id
from lyrveil.jobs.handlers import (
    GPU_INSUFFICIENT_VRAM,
    INTERNAL,
    LEASE_LOST,
    AbandoningHandler,
    JobDone,
    JobFailedError,
    JobHandler,
    JobOutcome,
    JobPreemptedError,
    NextStage,
    RunContext,
)
from lyrveil.jobs.policy import JobClass, JobKind, RequiredMode, policy_for
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.logs import get_logger
from lyrveil.orchestration.client import (
    HEARTBEAT_INTERVAL_S,
    BlockedVram,
    DesiredMode,
    GpuStatus,
    Lease,
    LeaseLostError,
    LeaseMode,
    LeaseOutcome,
    LeaseRefusedError,
    ModeRefusedError,
    OrchestratorUnavailableError,
)
from lyrveil.persistence.database import WRITE
from lyrveil.ports.text_runtime import TextRuntimeError

# How long the idle worker waits for notify() before it looks at the queue again: a backoff that ends, a GPU mode
# that changes or a lease that was refused all become eligible without a notification.
IDLE_POLL_S = 1.0
# docs/configuration.md, `vram.wait_seconds`: how long the job that needs a mode waits while the orchestrator holds its
# load back for VRAM. The default only: the setting is not read from application_settings yet.
VRAM_WAIT_S = 120.0
# Lease outcomes the orchestrator acts on (docs/gpu-orchestration.md, Load failures): "oom" moves to the next launch
# level, "runtime_error" makes it check the runtime.
_OOM_CODES = frozenset({"LLM_OOM", "IMAGE_OOM"})
_RUNTIME_ERROR_CODES = frozenset({"RUNTIME_UNAVAILABLE", "GENERATION_STALLED", "GENERATION_TIMEOUT"})

logger = get_logger(__name__)


class LeaseProvider(Protocol):
    """The lease calls of OrchestratorClient. They block, so the scheduler runs them in a worker thread."""

    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease: ...
    def renew_lease(self, lease: Lease) -> Lease: ...
    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool: ...


class ModeSwitch(Protocol):
    """The mode calls of OrchestratorClient. They block, so the scheduler runs them in a worker thread."""

    def read_state(self) -> GpuStatus: ...
    def request_mode(self, mode: DesiredMode, job_id: str | None = None) -> None: ...


_MODES: dict[LeaseMode, RequiredMode] = {"llm": RequiredMode.LLM, "image": RequiredMode.IMAGE}


def mode_deciding_job(connection: Connection, queue: JobQueue, kinds: Collection[JobKind]) -> Job | None:
    """Returns the queued job whose stage decides the GPU mode, among the eligible jobs of `kinds`, or None when no
    eligible job needs the GPU, which means the mode `llm` (docs/gpu-orchestration.md: with an empty queue, the desired
    mode is always `llm`).

    The reduced selection rule: a foreground job that needs `llm` comes first, then a job that needs `image`, then any
    other job that needs `llm`. So soft and background work never takes the GPU away from a waiting image, and an image
    never waits behind anything but foreground text work. An `image_request` in stage `prompt` is foreground text work.
    """
    foreground = [kind for kind in kinds if policy_for(kind).job_class is JobClass.FOREGROUND]
    for modes, among in (({RequiredMode.LLM}, foreground), ({RequiredMode.IMAGE}, kinds), ({RequiredMode.LLM}, kinds)):
        job = queue.next_eligible(connection, modes, among)
        if job is not None:
            return job
    return None


@dataclass
class _Run:
    outcome: JobOutcome | None = None
    error: Exception | None = None
    lease_lost: bool = False


@dataclass(frozen=True)
class _Overdue:
    """A job whose mode the GPU could not take within the VRAM wait, and the failure that ends it."""

    job: Job
    error: JobFailedError


def _vram_detail(blocked: BlockedVram | None) -> str:
    """Returns the Italian reason shown with GPU_INSUFFICIENT_VRAM, with the numbers of the orchestrator's last check
    when it sent them. It names memory sizes only, never private text."""
    if blocked is None:
        return "Altri programmi stanno usando la scheda video. Chiudili e riprova."
    return (
        f"Servono {blocked.required_mib} MiB liberi sulla scheda video, ma ne restano {blocked.free_mib}: "
        "altri programmi la stanno usando. Chiudili e riprova."
    )


def _lease_outcome(code: str | None) -> LeaseOutcome:
    if code in _OOM_CODES:
        return "oom"
    if code in _RUNTIME_ERROR_CODES:
        return "runtime_error"
    return "ok"


def _log_fields(job: Job) -> dict[str, str]:
    # A missing error code is left out, because the log allowlist would render None as "[dropped]".
    fields = {"job_id": job.id, "state": job.status.value}
    if job.error_code is not None:
        fields["error_code"] = job.error_code
    return fields


def read[T](engine: Engine, work: Callable[[Connection], T]) -> T:
    """Returns `work` run on a read transaction of `engine`, which is rolled back afterwards."""
    with engine.connect() as connection:
        try:
            return work(connection)
        finally:
            connection.rollback()


def write[T](engine: Engine, work: Callable[[Connection], T]) -> T:
    """Returns `work` run in one BEGIN IMMEDIATE transaction of `engine`, committed when `work` returns and rolled
    back when it raises."""
    with engine.connect() as connection:
        connection.execution_options(**WRITE)
        with connection.begin():
            return work(connection)


def recover_interrupted_jobs(engine: Engine, queue: JobQueue) -> Sequence[Job]:
    """Startup step 5 of docs/backend.md: applies JobQueue.recover_interrupted in one transaction and returns the
    recovered jobs. Must run before any worker starts."""
    recovered = write(engine, queue.recover_interrupted)
    for job in recovered:
        logger.info("job_recovered", **_log_fields(job))
    return recovered


class JobScheduler:
    """Runs one job at a time from `queue`.

    `handlers` gives the handler of each kind the backend can run; jobs of other kinds wait. `current_mode` returns the
    GPU mode whose runtime is ready, RequiredMode.NONE when none is. A stage that needs a mode runs under a lease of
    that mode, renewed every `heartbeat_interval_s`; losing it cancels the handler and retries the job with LEASE_LOST.

    With `mode_switch`, `current_mode` is not used: each pass reads the GPU state from the orchestrator and, when the
    desired mode differs from the one the queue needs (`mode_deciding_job`), asks for it with PUT /v1/desired-mode.
    While the GPU is not ready in that mode, only stages that need no GPU run. The orchestrator applies the change
    once the current lease ends, and verifies the release before it starts the other runtime, so the two never run
    together. When nothing queued needs the GPU, the state is read only until `llm` has been asked for once.

    While the orchestrator holds the load of that mode back with GPU_INSUFFICIENT_VRAM, the job that needs the mode
    waits `vram_wait_s` on the monotonic `clock`, from the first pass that sees the hold for it. Then it fails with
    that code, not retried, and a detail naming the free and the required memory; the next job waits anew.
    """

    def __init__(
        self,
        engine: Engine,
        queue: JobQueue,
        handlers: Mapping[JobKind, JobHandler],
        leases: LeaseProvider,
        current_mode: Callable[[], RequiredMode],
        *,
        mode_switch: ModeSwitch | None = None,
        heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
        idle_poll_s: float = IDLE_POLL_S,
        vram_wait_s: float = VRAM_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._queue = queue
        self._handlers = dict(handlers)
        self._leases = leases
        self._current_mode = current_mode
        self._mode_switch = mode_switch
        # The mode last seen as desired, or asked for, on the orchestrator; None until the first read.
        self._desired: DesiredMode | None = None
        self._heartbeat_interval_s = heartbeat_interval_s
        self._idle_poll_s = idle_poll_s
        self._vram_wait_s = vram_wait_s
        self._clock = clock
        # The job that waits while the orchestrator holds its mode's load back for VRAM, and when the wait began.
        self._vram_wait: tuple[str, float] | None = None
        self._wake = anyio.Event()

    def notify(self) -> None:
        """Wakes the idle worker at once, for example after a commit that queued a job. Call it on the event loop;
        from a worker thread, use anyio.from_thread.run_sync(scheduler.notify)."""
        self._wake.set()

    async def run_forever(self) -> None:
        """Runs jobs one at a time until cancelled. An unexpected error is logged and the loop goes on."""
        while True:
            try:
                ran = await self.run_once()
            except Exception:
                logger.error("job_worker_failed", exc_info=True)
                ran = None
            if ran is None:
                with anyio.move_on_after(self._idle_poll_s):
                    await self._wake.wait()
                self._wake = anyio.Event()

    async def run_once(self) -> Job | None:
        """Runs the best eligible job once and returns it in its new state.

        Returns None, and changes nothing in the queue, when no job is eligible, or when the orchestrator refuses the
        lease or does not answer: the job stays queued and is tried again later. With a mode switch, a pass that only
        asks for a mode change also returns None, and a pass that finds the VRAM wait over ends the waiting job
        instead of running one, and returns it failed.
        """
        kinds = tuple(self._handlers)
        if self._mode_switch is None:
            modes = {RequiredMode.NONE, self._current_mode()}
        else:
            modes, overdue = await self._follow_queue(self._mode_switch, kinds)
            if overdue is not None:
                return await self._abandon(overdue.job, overdue.error)
        candidate = await to_thread.run_sync(
            read, self._engine, lambda connection: self._queue.next_eligible(connection, modes, kinds)
        )
        if candidate is None:
            return None
        lease: Lease | None = None
        if candidate.required_mode is not RequiredMode.NONE:
            lease_mode: LeaseMode = "llm" if candidate.required_mode is RequiredMode.LLM else "image"
            try:
                lease = await to_thread.run_sync(self._leases.acquire_lease, lease_mode, candidate.id)
            except (LeaseRefusedError, OrchestratorUnavailableError):
                logger.info("job_waiting_for_lease", job_id=candidate.id)
                return None
        genkey = new_id()
        lease_id = None if lease is None else lease.id
        started = await to_thread.run_sync(
            write, self._engine, lambda connection: self._queue.start(connection, candidate.id, lease_id, genkey)
        )
        if started is None:
            if lease is not None:
                await self._release(lease, "ok")
            return None
        logger.info("job_started", job_id=started.id, state=started.status.value)
        run = await self._execute(started, RunContext(lease=lease, genkey=genkey))
        finished = await to_thread.run_sync(
            write, self._engine, lambda connection: self._store(connection, started, run)
        )
        if lease is not None and not run.lease_lost:
            await self._release(lease, _lease_outcome(finished.error_code))
        logger.info("job_run_ended", **_log_fields(finished))
        return finished

    async def _follow_queue(
        self, switch: ModeSwitch, kinds: Collection[JobKind]
    ) -> tuple[set[RequiredMode], _Overdue | None]:
        """Asks for the mode the queue needs when the orchestrator is not already moving to it, and returns the modes
        whose stages may run now: that mode once its runtime is ready, and always RequiredMode.NONE. The second value
        is the job to end when its VRAM wait is over (`_vram_overdue`), else None.

        A state read or a mode request that fails is logged and leaves only RequiredMode.NONE, so the GPU jobs wait
        for the next pass; it neither starts nor ends a VRAM wait.
        """
        deciding = await to_thread.run_sync(
            read, self._engine, lambda connection: mode_deciding_job(connection, self._queue, kinds)
        )
        needed: LeaseMode = "image" if deciding is not None and deciding.required_mode is RequiredMode.IMAGE else "llm"
        if deciding is None and self._desired == "llm":
            self._vram_wait = None
            return {RequiredMode.NONE}, None
        try:
            status = await to_thread.run_sync(switch.read_state)
            self._desired = status.desired_mode
            if status.desired_mode != needed and not status.draining:
                job_id = None if deciding is None else deciding.id
                await to_thread.run_sync(switch.request_mode, needed, job_id)
                self._desired = needed
                # `state` carries the requested mode, since the log allowlist has no mode field.
                logger.info("gpu_mode_requested", state=needed, **({} if job_id is None else {"job_id": job_id}))
        except (ModeRefusedError, OrchestratorUnavailableError):
            logger.info("gpu_mode_unavailable")
            return {RequiredMode.NONE}, None
        if status.ready_mode == needed and status.desired_mode == needed:
            self._vram_wait = None
            return {RequiredMode.NONE, _MODES[needed]}, None
        return {RequiredMode.NONE}, self._vram_overdue(deciding, needed, status)

    def _vram_overdue(self, deciding: Job | None, needed: LeaseMode, status: GpuStatus) -> _Overdue | None:
        """Times the wait of `deciding` while the orchestrator holds the load of `needed` back for VRAM
        (docs/gpu-orchestration.md, Verifying GPU release), and returns it with its failure once the wait has lasted
        `vram_wait_s`. The wait begins at the first pass that sees the hold for this job, and a pass without the hold
        ends it, so each job gets a whole wait of its own."""
        blocked = status.blocked_vram
        if (
            deciding is None
            or status.blocked_reason != GPU_INSUFFICIENT_VRAM
            or status.desired_mode != needed
            or (blocked is not None and blocked.mode != needed)
        ):
            self._vram_wait = None
            return None
        now = self._clock()
        if self._vram_wait is None or self._vram_wait[0] != deciding.id:
            self._vram_wait = (deciding.id, now)
            logger.info("job_waiting_for_vram", job_id=deciding.id)
            return None
        if now - self._vram_wait[1] < self._vram_wait_s:
            return None
        self._vram_wait = None
        return _Overdue(deciding, JobFailedError(GPU_INSUFFICIENT_VRAM, _vram_detail(blocked), retryable=False))

    async def _abandon(self, job: Job, error: JobFailedError) -> Job | None:
        """Ends the queued `job` with `error` without running its stage, and returns it failed, or None when it is no
        longer queued. It is marked running without a lease, so no runtime is touched; an AbandoningHandler then
        records the failure on what the job shows the user, and the failure is stored as for any run."""
        genkey = new_id()
        started = await to_thread.run_sync(
            write, self._engine, lambda connection: self._queue.start(connection, job.id, None, genkey)
        )
        if started is None:
            return None
        handler = self._handlers[started.kind]
        if isinstance(handler, AbandoningHandler):
            try:
                await handler.abandon(started, error)
            except Exception:
                logger.error("job_abandon_failed", job_id=started.id, exc_info=True)
        finished = await to_thread.run_sync(
            write, self._engine, lambda connection: self._store(connection, started, _Run(error=error))
        )
        logger.info("job_run_ended", **_log_fields(finished))
        return finished

    async def _execute(self, job: Job, context: RunContext) -> _Run:
        run = _Run()
        handler = self._handlers[job.kind]
        async with anyio.create_task_group() as group:
            if context.lease is not None:
                group.start_soon(self._heartbeat, context.lease, run, group.cancel_scope)
            try:
                run.outcome = await handler(job, context)
            except Exception as error:
                run.error = error
            group.cancel_scope.cancel()
        return run

    async def _heartbeat(self, lease: Lease, run: _Run, scope: anyio.CancelScope) -> None:
        while True:
            await anyio.sleep(self._heartbeat_interval_s)
            try:
                lease = await to_thread.run_sync(self._leases.renew_lease, lease)
            except LeaseLostError:
                # The runtime may now serve another lease: the handler must stop touching it at once.
                run.lease_lost = True
                scope.cancel()
                return
            except OrchestratorUnavailableError:
                # The lease expires by itself after its TTL; the next heartbeat then reports it lost.
                logger.warning("job_heartbeat_unanswered", job_id=lease.job_id)

    def _store(self, connection: Connection, job: Job, run: _Run) -> Job:
        outcome, error = run.outcome, run.error
        if isinstance(outcome, JobDone):
            return self._queue.complete(connection, job.id, outcome.result)
        if isinstance(outcome, NextStage):
            try:
                return self._queue.advance(connection, job.id, outcome.stage, outcome.payload)
            except ValueError:
                logger.error("job_unknown_stage", job_id=job.id)
                return self._queue.fail(connection, job.id, INTERNAL, None, retryable=False)
        if run.lease_lost:
            return self._queue.fail(connection, job.id, LEASE_LOST, None, retryable=True)
        if isinstance(error, JobPreemptedError):
            return self._queue.requeue_preempted(connection, job.id)
        if isinstance(error, JobFailedError):
            return self._queue.fail(connection, job.id, error.code, error.detail, retryable=error.retryable)
        if isinstance(error, TextRuntimeError):
            return self._queue.fail(connection, job.id, error.code, None, retryable=True)
        logger.error("job_handler_failed", job_id=job.id, exc_info=error)
        return self._queue.fail(connection, job.id, INTERNAL, None, retryable=False)

    async def _release(self, lease: Lease, outcome: LeaseOutcome) -> None:
        try:
            await to_thread.run_sync(self._leases.release_lease, lease, outcome)
        except OrchestratorUnavailableError:
            # Nothing else to do: an unreleased lease expires by itself after its TTL.
            logger.warning("job_lease_release_unanswered", job_id=lease.job_id)
