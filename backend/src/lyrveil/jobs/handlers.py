"""The contract between JobScheduler and a job handler: what a handler receives, returns and raises."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from lyrveil.jobs.queue import Job
from lyrveil.orchestration.client import Lease

# docs/api.md (Errors): the job error a handler bug ends with. Details stay in the logs.
INTERNAL = "INTERNAL"
LEASE_LOST = "LEASE_LOST"


@dataclass(frozen=True)
class RunContext:
    """What one run of a job holds: the lease of its mode, None for a `none` stage, and the abort key of the run."""

    lease: Lease | None
    genkey: str


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
    """

    def __init__(self, code: str, detail: str | None = None, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.retryable = retryable


class JobPreemptedError(Exception):
    """The run was aborted to let foreground work through. The job is queued again and keeps its attempt."""


class JobHandler(Protocol):
    """Runs one stage of a job and returns its outcome, which the scheduler then stores.

    The handler writes its own results in its own transactions, and must be idempotent per stage: a restart between
    its commit and the scheduler's state change runs the stage again (persistence keyed by the job id, as
    docs/data-model.md does for images). Raises JobFailedError, JobPreemptedError or a TextRuntimeError, which is
    retried with its code; any other exception ends the job with INTERNAL.
    """

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome: ...
