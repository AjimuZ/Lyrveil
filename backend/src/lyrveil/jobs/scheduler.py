"""JobScheduler: the single worker that picks an eligible job, holds its lease, runs its handler and stores the outcome.

The base rule: a job runs when its stage needs no GPU or needs the mode the GPU is in, and a handler for its kind is
registered; anything else waits in the queue. With a ModeSwitch (P05.7), the scheduler also asks the orchestrator for
the mode its queue needs. Since P06.3 it follows the rules of docs/gpu-orchestration.md (Selection) through
lyrveil.jobs.selection: images in batches, the image wait limit, the cap on images in a row, and soft and background
work only once the user is idle. Since P06.2 it ends the job that waits for that mode once the orchestrator has held
the load back for VRAM during `vram_wait_s`. Since P06.4 a text run out of memory lowers the launch level before its
lease is released, and an image job whose runtime failed its last load retry ends with IMAGE_LOAD_FAILED.

While a GPU stage runs, the scheduler watches for the two reasons to stop it (P06.3): foreground work that arrives
during background work preempts it, and the user's cancellation ends it `cancelled`. Either way it cancels the
handler, asks the runtime to abort the run's work under the lease it still holds, then stores the outcome.
"""

import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
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
    CancellingHandler,
    JobDone,
    JobFailedError,
    JobHandler,
    JobOutcome,
    JobPreemptedError,
    NextStage,
    RunContext,
    StopReason,
)
from lyrveil.jobs.policy import JobClass, JobKind, JobStatus, RequiredMode, policy_for
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.selection import (
    Decision,
    GpuMode,
    ImageBatch,
    QueueSnapshot,
    SchedulingRules,
    decide,
    preempts,
)
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
from lyrveil.ports.image_runtime import ImageRuntime, ImageRuntimeError
from lyrveil.ports.text_runtime import TextRuntime, TextRuntimeError

# How long the idle worker waits for notify() before it looks at the queue again: a backoff that ends, a GPU mode
# that changes or a lease that was refused all become eligible without a notification. A running GPU stage is checked
# for a preemption or a cancellation as often.
IDLE_POLL_S = 1.0
# docs/configuration.md, `vram.wait_seconds`: how long the job that needs a mode waits while the orchestrator holds its
# load back for VRAM. The default only: the setting is not read from application_settings yet.
VRAM_WAIT_S = 120.0
# docs/gpu-orchestration.md (Load failures): the job error of an image whose runtime failed its last load retry, and
# the Italian reason shown with it. It names no private text.
IMAGE_LOAD_FAILED = "IMAGE_LOAD_FAILED"
_IMAGE_LOAD_DETAIL = "Il generatore di immagini non è riuscito a partire. Riprova più tardi."
# Lease outcomes the orchestrator acts on (docs/gpu-orchestration.md, Load failures): "oom" moves to the next launch
# level, "runtime_error" makes it check the runtime.
_OOM_CODES = frozenset({"LLM_OOM", "IMAGE_OOM"})
_RUNTIME_ERROR_CODES = frozenset({"RUNTIME_UNAVAILABLE", "GENERATION_STALLED", "GENERATION_TIMEOUT"})
_ALL_MODES = frozenset(RequiredMode)

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


class LaunchLevelLowering(Protocol):
    """LaunchLevels.lower: moves the text runtime's launch file to the next level. It blocks, so the scheduler runs it
    in a worker thread; it reports its own failures and never raises for them."""

    def lower(self) -> object: ...


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


@dataclass(frozen=True)
class _Plan:
    """What a pass of `_follow_queue` allows: `run`, the GPU job that may run now (None when only stages without a GPU
    may), and `overdue`, the job to end because its VRAM wait is over."""

    run: Job | None = None
    overdue: _Overdue | None = None


@dataclass(frozen=True)
class _Groups:
    """The eligible queued jobs of one pass, as the selection rules group them."""

    foreground: Job | None
    other: Job | None
    images: Sequence[Job]


def _vram_detail(blocked: BlockedVram | None) -> str:
    """Returns the Italian reason shown with GPU_INSUFFICIENT_VRAM, with the numbers of the orchestrator's last check
    when it sent them. It names memory sizes only, never private text."""
    if blocked is None:
        return "Altri programmi stanno usando la scheda video. Chiudili e riprova."
    return (
        f"Servono {blocked.required_mib} MiB liberi sulla scheda video, ma ne restano {blocked.free_mib}: "
        "altri programmi la stanno usando. Chiudili e riprova."
    )


def _image_load_failed(status: GpuStatus, images: Sequence[Job]) -> _Overdue | None:
    """Returns the queued image job to end because the orchestrator reports that the image runtime failed its last
    load retry for it (a final `load_failure` of mode `image` naming the job), with IMAGE_LOAD_FAILED; None otherwise.

    The orchestrator keeps reporting the failure until the image runtime next loads, so a job that already ended is
    simply not found among `images`: each job ends once, also across a backend restart.
    """
    failure = status.load_failure
    if failure is None or failure.mode != "image" or not failure.final or failure.job_id is None:
        return None
    job = next((job for job in images if job.id == failure.job_id), None)
    if job is None:
        return None
    return _Overdue(job, JobFailedError(IMAGE_LOAD_FAILED, _IMAGE_LOAD_DETAIL, retryable=False))


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


def _queue_order(job: Job) -> tuple[int, datetime, str]:
    return job.priority, job.created_at, job.id


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

    With `mode_switch`, `current_mode` is not used: each pass reads the GPU state from the orchestrator, decides with
    lyrveil.jobs.selection under `rules`, and when the desired mode differs from the decision's, asks for it with PUT
    /v1/desired-mode. While the GPU is not ready in that mode, only stages that need no GPU run. The orchestrator
    applies the change once the current lease ends, and verifies the release before it starts the other runtime, so the
    two never run together. When nothing queued needs the GPU, the state is read only until `llm` has been asked for
    once. Without a mode switch, the best eligible job of the current mode runs, whatever its class.

    The selection's clocks run on the monotonic `clock` and live in memory, so a restart starts them again:
    - an image's wait counts from the first pass that sees it in an image stage, and only while the text runtime is
      ready, so every visit to text mode gives foreground text `image_max_wait_s` before an image takes the GPU back;
    - the user is idle from the last pass that saw foreground text queued, or the end of the last foreground run;
    - a batch of images starts when the scheduler asks for `image`.

    While the orchestrator holds the load of that mode back with GPU_INSUFFICIENT_VRAM, the job that needs the mode
    waits `vram_wait_s` from the first pass that sees the hold for it. Then it fails with that code, not retried, and a
    detail naming the free and the required memory; the next job waits anew.

    A running GPU stage is stopped on purpose for two reasons, checked at each `notify` and every `idle_poll_s`: a
    foreground job queued after a background generation started preempts it (it is queued again at no attempt cost),
    and a cancellation requested with JobQueue.request_cancel ends it `cancelled`. The handler is cancelled with
    `context.stop` telling why, then `text_runtime.abort(genkey)` or `image_runtime.interrupt()` ends the runtime's
    work before the lease is released. A CancellingHandler then removes what the job showed.

    Out of memory (P06.4, docs/gpu-orchestration.md, Load failures and launch levels): a text run that ends with
    LLM_OOM first lowers the launch level through `launch_levels`, then releases its lease with `oom`, after which the
    orchestrator restarts the runtime with the new file. When the orchestrator reports that the image runtime failed
    its last load retry for a queued image job, that job ends with IMAGE_LOAD_FAILED, not retried, as the VRAM wait
    ends one; the orchestrator has already given the GPU back to mode `llm`, and the next image job asks again.
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
        rules: SchedulingRules | None = None,
        text_runtime: TextRuntime | None = None,
        image_runtime: ImageRuntime | None = None,
        launch_levels: LaunchLevelLowering | None = None,
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
        self._rules = rules or SchedulingRules()
        self._text_runtime = text_runtime
        self._image_runtime = image_runtime
        self._launch_levels = launch_levels
        kinds = tuple(self._handlers)
        self._foreground_kinds = tuple(kind for kind in kinds if policy_for(kind).job_class is JobClass.FOREGROUND)
        self._other_kinds = tuple(kind for kind in kinds if kind not in self._foreground_kinds)
        # The mode last seen as desired, or asked for, on the orchestrator; None until the first read.
        self._desired: DesiredMode | None = None
        self._heartbeat_interval_s = heartbeat_interval_s
        self._idle_poll_s = idle_poll_s
        self._vram_wait_s = vram_wait_s
        self._clock = clock
        # The job that waits while the orchestrator holds its mode's load back for VRAM, and when the wait began.
        self._vram_wait: tuple[str, float] | None = None
        # The selection's memory: the current image batch, when each queued image job was first seen in its image
        # stage, since when the text runtime has been ready, and the last moment of foreground activity.
        self._batch = ImageBatch()
        self._image_seen: dict[str, float] = {}
        self._text_since: float | None = None
        self._active_at: float | None = None
        self._wake = anyio.Event()
        # Set by notify() while a GPU stage runs, so its watch looks at the queue at once.
        self._changed: anyio.Event | None = None

    def notify(self) -> None:
        """Wakes the idle worker at once, for example after a commit that queued a job, and makes a running GPU stage
        check for a preemption or a cancellation. Call it on the event loop; from a worker thread, use
        anyio.from_thread.run_sync(scheduler.notify)."""
        self._wake.set()
        if self._changed is not None:
            self._changed.set()

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
        instead of running one, and returns it failed. A queued job left with a cancellation request is ended
        `cancelled` first, and returned.
        """
        kinds = tuple(self._handlers)
        pending = await to_thread.run_sync(
            read, self._engine, lambda connection: self._queue.next_cancel_pending(connection, kinds)
        )
        if pending is not None:
            return await self._finish_cancelled(pending)
        if self._mode_switch is None:
            modes = {RequiredMode.NONE, self._current_mode()}
            candidate = await to_thread.run_sync(
                read, self._engine, lambda connection: self._queue.next_eligible(connection, modes, kinds)
            )
        else:
            plan = await self._follow_queue(self._mode_switch)
            if plan.overdue is not None:
                return await self._abandon(plan.overdue.job, plan.overdue.error)
            candidate = await to_thread.run_sync(
                read, self._engine, lambda connection: self._first(connection, plan.run, kinds)
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
        context = RunContext(lease=lease, genkey=genkey)
        run = await self._execute(started, context)
        reason = context.stop.reason
        if reason is not None and lease is not None and not run.lease_lost:
            await self._stop_runtime(started, genkey)
        finished = await to_thread.run_sync(
            write, self._engine, lambda connection: self._store(connection, started, run, reason)
        )
        if lease is not None and not run.lease_lost:
            outcome = _lease_outcome(finished.error_code)
            if outcome == "oom" and lease.mode == "llm":
                await self._lower_launch_level(started)
            await self._release(lease, outcome)
        self._ran(started)
        if finished.status is JobStatus.CANCELLED:
            await self._announce_cancelled(finished)
        logger.info("job_run_ended", **_log_fields(finished))
        return finished

    def _first(self, connection: Connection, gpu_job: Job | None, kinds: Collection[JobKind]) -> Job | None:
        """Returns whichever comes first in queue order: `gpu_job`, the GPU job the selection allows now, or the best
        eligible stage that needs no GPU."""
        without_gpu = self._queue.next_eligible(connection, {RequiredMode.NONE}, kinds)
        candidates = [job for job in (gpu_job, without_gpu) if job is not None]
        return min(candidates, key=_queue_order, default=None)

    def _groups(self, connection: Connection) -> _Groups:
        return _Groups(
            foreground=self._queue.next_eligible(connection, {RequiredMode.LLM}, self._foreground_kinds),
            other=self._queue.next_eligible(connection, {RequiredMode.LLM}, self._other_kinds),
            images=self._queue.eligible(connection, {RequiredMode.IMAGE}, tuple(self._handlers)),
        )

    async def _follow_queue(self, switch: ModeSwitch) -> _Plan:
        """Decides what the queue needs (lyrveil.jobs.selection.decide), asks for its mode when the orchestrator is not
        already moving to it, and returns what may run now: the decision's GPU job once its mode is ready, and always
        the stages that need no GPU. The plan names the job to end when its VRAM wait is over (`_vram_overdue`).

        A state read or a mode request that fails is logged and leaves only the stages without a GPU, so the GPU jobs
        wait for the next pass; it neither starts nor ends a VRAM wait.
        """
        groups = await to_thread.run_sync(read, self._engine, self._groups)
        now = self._clock()
        if groups.foreground is not None:
            self._active_at = now
        self._see_images(groups.images, now)
        if groups.foreground is None and groups.other is None and not groups.images and self._desired == "llm":
            self._vram_wait = None
            return _Plan()
        try:
            status = await to_thread.run_sync(switch.read_state)
            self._desired = status.desired_mode
            failed = _image_load_failed(status, groups.images)
            if failed is not None:
                # Before any decision: the job would otherwise ask for the image mode again.
                return _Plan(overdue=failed)
            self._see_text_mode(status, now)
            decision = self._decide(status, groups, now)
            if status.desired_mode != decision.mode and not status.draining:
                job_id = None if decision.deciding is None else decision.deciding.id
                await to_thread.run_sync(switch.request_mode, decision.mode, job_id)
                self._desired = decision.mode
                if decision.mode == "image":
                    self._batch = ImageBatch()
                # `state` carries the requested mode, since the log allowlist has no mode field.
                logger.info("gpu_mode_requested", state=decision.mode, **({} if job_id is None else {"job_id": job_id}))
        except (ModeRefusedError, OrchestratorUnavailableError):
            logger.info("gpu_mode_unavailable")
            return _Plan()
        if status.ready_mode == decision.mode and status.desired_mode == decision.mode:
            self._vram_wait = None
            return _Plan(run=decision.run)
        return _Plan(overdue=self._vram_overdue(decision.deciding, decision.mode, status))

    def _decide(self, status: GpuStatus, groups: _Groups, now: float) -> Decision:
        """Applies the selection rules to this pass, and remembers a batch that ends at the cap."""
        mode: GpuMode = "image" if status.desired_mode == "image" else "llm"
        snapshot = QueueSnapshot(
            foreground_text=groups.foreground,
            other_text=groups.other,
            image=groups.images[0] if groups.images else None,
            image_waited_s=self._image_waited(groups.images, now),
        )
        idle_s = None if self._active_at is None else now - self._active_at
        decision = decide(mode, snapshot, self._batch, idle_s, self._rules)
        if decision.caps_batch:
            # The count stays, so image mode keeps asking for text until the switch happens.
            self._batch = replace(self._batch, capped=True)
        return decision

    def _see_images(self, images: Sequence[Job], now: float) -> None:
        """Keeps when each queued image job was first seen in its image stage, for the jobs still queued there."""
        seen = {job.id: self._image_seen.get(job.id, now) for job in images}
        self._image_seen = seen

    def _see_text_mode(self, status: GpuStatus, now: float) -> None:
        """Starts the text-mode clock at the first pass that finds the text runtime ready and wanted, and stops it
        when the desired mode is `image`. While the text runtime loads, the clock stays as it was."""
        if status.desired_mode == "image":
            self._text_since = None
        elif status.desired_mode == "llm" and status.ready_mode == "llm" and self._text_since is None:
            self._text_since = now

    def _image_waited(self, images: Sequence[Job], now: float) -> float:
        """Returns how long the image that has waited longest has waited while the text runtime was ready."""
        if not images or self._text_since is None:
            return 0.0
        oldest = min(self._image_seen[job.id] for job in images)
        return max(0.0, now - max(oldest, self._text_since))

    def _ran(self, job: Job) -> None:
        """Counts a finished run for the selection: an image stage adds to the batch, text work ends a capped batch,
        and the end of a foreground run is the user's latest activity."""
        if job.required_mode is RequiredMode.IMAGE:
            self._batch = replace(self._batch, images_in_a_row=self._batch.images_in_a_row + 1)
        elif job.required_mode is RequiredMode.LLM:
            self._batch = ImageBatch()
        if policy_for(job.kind).job_class is JobClass.FOREGROUND:
            self._active_at = self._clock()

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
            write, self._engine, lambda connection: self._store(connection, started, _Run(error=error), None)
        )
        logger.info("job_run_ended", **_log_fields(finished))
        return finished

    async def _finish_cancelled(self, job: Job) -> Job | None:
        """Ends a queued job whose cancellation a run left pending, and returns it `cancelled`, or None when it is no
        longer queued."""
        cancelled = await to_thread.run_sync(
            write, self._engine, lambda connection: self._queue.cancel_queued(connection, job.id)
        )
        if cancelled is None:
            return None
        await self._announce_cancelled(cancelled)
        logger.info("job_run_ended", **_log_fields(cancelled))
        return cancelled

    async def _announce_cancelled(self, job: Job) -> None:
        handler = self._handlers.get(job.kind)
        if isinstance(handler, CancellingHandler):
            try:
                await handler.cancelled(job)
            except Exception:
                logger.error("job_cancel_not_recorded", job_id=job.id, exc_info=True)

    async def _execute(self, job: Job, context: RunContext) -> _Run:
        run = _Run()
        handler = self._handlers[job.kind]
        async with anyio.create_task_group() as group:
            if context.lease is not None:
                group.start_soon(self._heartbeat, context.lease, run, group.cancel_scope)
                group.start_soon(self._watch, job, context, group.cancel_scope)
            try:
                run.outcome = await handler(job, context)
            except Exception as error:
                run.error = error
            group.cancel_scope.cancel()
        self._changed = None
        return run

    async def _watch(self, job: Job, context: RunContext, scope: anyio.CancelScope) -> None:
        """Checks the running `job` at once, at every notify() and every `idle_poll_s`, and stops it with the reason
        `_stop_reason` finds: `context.stop.reason` is set before the handler is cancelled."""
        while True:
            self._changed = anyio.Event()
            reason = await to_thread.run_sync(read, self._engine, lambda connection: self._stop_reason(connection, job))
            if reason is not None:
                logger.info("job_stopping", job_id=job.id, state=reason.value)
                context.stop.reason = reason
                scope.cancel()
                return
            with anyio.move_on_after(self._idle_poll_s):
                await self._changed.wait()

    def _stop_reason(self, connection: Connection, job: Job) -> StopReason | None:
        """Returns why the running `job` must stop now, or None. A cancellation request wins. Only a background
        generation is preempted, and only by a foreground job queued after it started, so a job left queued on
        purpose, such as an image after a capped batch, never stops the same work twice."""
        current = self._queue.get(connection, job.id)
        if current is not None and current.cancel_requested_at is not None:
            return StopReason.CANCELLED
        running = policy_for(job.kind).job_class
        if job.required_mode is not RequiredMode.LLM or not preempts(running, JobClass.FOREGROUND):
            return None
        since = job.started_at
        for queued in self._queue.eligible(connection, _ALL_MODES, self._foreground_kinds):
            if since is None or queued.created_at > since:
                return StopReason.PREEMPTED
        return None

    async def _stop_runtime(self, job: Job, genkey: str) -> None:
        """Asks the runtime to end the work of a run the scheduler stopped, under the run's lease, which is still held:
        POST /api/extra/abort with the run's genkey for a text stage, POST /interrupt for an image stage. A failure is
        logged: the generation then ends on its own, and the next lease waits for it."""
        try:
            if job.required_mode is RequiredMode.LLM and self._text_runtime is not None:
                await self._text_runtime.abort(genkey)
            elif job.required_mode is RequiredMode.IMAGE and self._image_runtime is not None:
                await self._image_runtime.interrupt()
        except (TextRuntimeError, ImageRuntimeError):
            logger.warning("job_runtime_stop_failed", job_id=job.id)

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

    def _store(self, connection: Connection, job: Job, run: _Run, reason: StopReason | None) -> Job:
        """Stores the end of a run. A finished job is completed even when a cancellation came too late to stop it;
        otherwise a cancellation, requested before the run ended or stored meanwhile, ends the job `cancelled`."""
        outcome, error = run.outcome, run.error
        if isinstance(outcome, JobDone):
            return self._queue.complete(connection, job.id, outcome.result)
        current = self._queue.get(connection, job.id)
        if reason is StopReason.CANCELLED or (current is not None and current.cancel_requested_at is not None):
            return self._queue.end_cancelled(connection, job.id)
        if isinstance(outcome, NextStage):
            try:
                return self._queue.advance(connection, job.id, outcome.stage, outcome.payload)
            except ValueError:
                logger.error("job_unknown_stage", job_id=job.id)
                return self._queue.fail(connection, job.id, INTERNAL, None, retryable=False)
        if run.lease_lost:
            return self._queue.fail(connection, job.id, LEASE_LOST, None, retryable=True)
        if reason is StopReason.PREEMPTED or isinstance(error, JobPreemptedError):
            return self._queue.requeue_preempted(connection, job.id)
        if isinstance(error, JobFailedError):
            return self._queue.fail(
                connection, job.id, error.code, error.detail, retryable=error.retryable, fallback=error.fallback
            )
        if isinstance(error, TextRuntimeError):
            return self._queue.fail(connection, job.id, error.code, None, retryable=True)
        logger.error("job_handler_failed", job_id=job.id, exc_info=error)
        return self._queue.fail(connection, job.id, INTERNAL, None, retryable=False)

    async def _lower_launch_level(self, job: Job) -> None:
        """Lowers the launch level after a text run that ran out of memory, before its lease is released, so the
        orchestrator's restart reads the new file. Without `launch_levels` nothing changes."""
        if self._launch_levels is None:
            return
        try:
            await to_thread.run_sync(self._launch_levels.lower)
        except Exception:
            logger.error("launch_level_not_lowered", job_id=job.id, exc_info=True)

    async def _release(self, lease: Lease, outcome: LeaseOutcome) -> None:
        try:
            await to_thread.run_sync(self._leases.release_lease, lease, outcome)
        except OrchestratorUnavailableError:
            # Nothing else to do: an unreleased lease expires by itself after its TTL.
            logger.warning("job_lease_release_unanswered", job_id=lease.job_id)
