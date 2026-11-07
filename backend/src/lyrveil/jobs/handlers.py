"""The contract between JobScheduler and a job handler: what a handler receives, returns and raises."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from lyrveil.jobs.queue import Job
from lyrveil.orchestration.client import Lease

# docs/api.md (Errors): the job error a handler bug ends with. Details stay in the logs.
INTERNAL = "INTERNAL"
LEASE_LOST = "LEASE_LOST"
# The orchestrator held the load back because other programs hold the VRAM, for longer than the job may wait.
GPU_INSUFFICIENT_VRAM = "GPU_INSUFFICIENT_VRAM"


class StopReason(StrEnum):
    """Why the scheduler cancelled a run before it ended (docs/gpu-orchestration.md, Preemption and Cancellation)."""

    CANCELLED = "cancelled"  # The user cancelled the job; it ends `cancelled`.
    PREEMPTED = "preempted"  # Foreground work arrived during background work; the job is queued again.


@dataclass
class RunStop:
    """Set by the scheduler just before it cancels a run that it stops on purpose; `reason` stays None otherwise, so
    a handler that sees the cancellation with no reason was cancelled for a lost lease or a shutdown."""

    reason: StopReason | None = None


@dataclass(frozen=True)
class RunContext:
    """What one run of a job holds: the lease of its mode, None for a `none` stage, the abort key of the run, and
    `stop`, which tells a cancelled handler why the scheduler stopped it."""

    lease: Lease | None
    genkey: str
    stop: RunStop = field(default_factory=RunStop)


@dataclass(frozen=True)
class JobDone:
    """The job is finished; `result` is stored in `result_json`."""

    result: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class NextStage:
    """The current stage is finished; the job waits again in `stage`. A `payload` replaces the stored one."""

    stage: str
    payload: Mapping[str, Any] | None = field(default=None)


JobOutcome = JobDone | NextStage


class JobFailedError(Exception):
    """A run failed with a job error `code` of docs/api.md (Errors).

    `retryable` failures are retried within the kind's attempts and backoff; others end the job at once. `detail` is
    shown to the user and must never hold private text: no message, prompt or memory content.

    `fallback`, on a retryable failure, is the payload of one more run of the same stage, after the kind's backoff and
    with a fresh attempt count, whatever attempts remain: a retry that does the stage another way, such as the
    **Rapida** try after a second out of memory (docs/image-generation.md, Failures). The handler decides when a
    fallback has been used, so the chain ends.
    """

    def __init__(
        self,
        code: str,
        detail: str | None = None,
        *,
        retryable: bool,
        fallback: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.fallback = fallback


class JobPreemptedError(Exception):
    """The run was aborted to let foreground work through. The job is queued again and keeps its attempt."""


class JobHandler(Protocol):
    """Runs one stage of a job and returns its outcome, which the scheduler then stores.

    The handler writes its own results in its own transactions, and must be idempotent per stage: a restart between
    its commit and the scheduler's state change runs the stage again (persistence keyed by the job id, as
    docs/data-model.md does for images). Raises JobFailedError, JobPreemptedError or a TextRuntimeError, which is
    retried with its code; any other exception ends the job with INTERNAL. The scheduler stops a run by cancelling it:
    for a lost lease, a shutdown, a preemption or the user's cancellation, which `context.stop` tells apart. After a
    preemption or a cancellation, the scheduler aborts the work the runtime still does for the run.
    """

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome: ...


@runtime_checkable
class AbandoningHandler(Protocol):
    """A handler that shows its job to the user, and so must record a failure that comes before its stage runs.

    The scheduler calls `abandon` when it ends a job that never got the GPU, such as GPU_INSUFFICIENT_VRAM after the
    VRAM wait: the job is running, no lease is held, and `error` is not retryable. `abandon` must not touch a runtime.
    It records the failure on what the job shows, such as its message, and announces it; the scheduler then stores the
    job's failure. An exception it raises is logged and does not keep the job from failing.
    """

    async def abandon(self, job: Job, error: JobFailedError) -> None: ...


@runtime_checkable
class CancellingHandler(Protocol):
    """A handler whose job shows a placeholder the user must stop seeing once the job is cancelled.

    The scheduler calls `cancelled` after it stored the job `cancelled` without the handler recording it: a run it
    stopped for the user, whose runtime work it has already aborted, or a queued job left with a cancellation
    request. The lease, if any, is still held, but `cancelled` must not touch a runtime. It removes what the job
    shows, such as its placeholder message, and announces it. An exception it raises is logged and changes nothing
    else.
    """

    async def cancelled(self, job: Job) -> None: ...
