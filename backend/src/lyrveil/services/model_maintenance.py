"""ModelMaintenance: the handler of the `maintenance` jobs that ModelProfiles queues, a reload and a custom file's hash.

`model_reload`, **Applica ora** (docs/models.md, Selecting a profile): the stage needs no GPU and holds no lease, and
the worker runs one job at a time, so no reply holds the runtime while it runs. It checks, before any mode change, that
the launch file's model file is present with the expected size (`MODEL_FILE_MISSING` otherwise). It then sets the
desired mode to `none`, waits until the orchestrator reports IDLE, which it enters only once the text runtime has
stopped and the GPU release is verified, and sets the mode to `llm`, so the runtime starts again and reads the new
launch file. The job ends once `llm` is asked for; the loading itself is followed by the GPU state and the next text
job's lease, as for any start.

`custom_model_hash` (docs/models.md, Custom profiles): hashes the file and stores the profile with
ModelProfiles.store_hashed. The hash runs in a worker thread the shutdown does not wait for.
"""

import contextlib
import time
from collections.abc import Callable
from typing import Any, Protocol, cast, get_args

from anyio import to_thread

from lyrveil.catalog import TextTemplate
from lyrveil.jobs.handlers import INTERNAL, JobDone, JobFailedError, JobOutcome, RunContext
from lyrveil.jobs.queue import Job
from lyrveil.logs import get_logger
from lyrveil.model_files import FOLDERS, ModelFileMissingError, ModelFileRefusedError, ModelKind
from lyrveil.orchestration.client import (
    MAX_STATE_WAIT_S,
    DesiredMode,
    GpuStatus,
    ModeRefusedError,
    OrchestratorUnavailableError,
)
from lyrveil.services.model_profiles import CUSTOM_HASH, RELOAD, ModelProfiles, ProfileUnavailableError

# docs/api.md (Errors), the job errors of these jobs.
MODEL_FILE_MISSING = "MODEL_FILE_MISSING"
GPU_RELEASE_UNVERIFIED = "GPU_RELEASE_UNVERIFIED"
ORCHESTRATOR_UNAVAILABLE = "ORCHESTRATOR_UNAVAILABLE"
# How long a reload waits for the stop and the verified release. The orchestrator's own stop and settle limits are
# shorter, so a reload that waits this long has met a GPU that is stuck.
RELEASE_TIMEOUT_S = 180.0
_TEMPLATES: tuple[str, ...] = get_args(TextTemplate)
_UNANSWERED = "Il controllo della scheda video non risponde. Riprova più tardi."
_DRAINING = "Lyrveil si sta fermando: il modello verrà caricato al prossimo avvio."
_UNRELEASED = "La scheda video non si è liberata in tempo. Controlla Risorse AI e riprova."

logger = get_logger(__name__)


class ModeControl(Protocol):
    """The mode calls of OrchestratorClient a reload uses. They block, so the handler runs them in a worker thread."""

    def read_state(self) -> GpuStatus: ...
    def request_mode(self, mode: DesiredMode, job_id: str | None = None) -> None: ...
    def wait_for_state(self, since: int, wait_s: float) -> GpuStatus: ...


class ModelMaintenance:
    """Runs the `maintenance` jobs of ModelProfiles over `profiles` and the orchestrator's `modes`. Any other
    `operation` ends the job with INTERNAL, not retried."""

    def __init__(
        self,
        profiles: ModelProfiles,
        modes: ModeControl,
        *,
        release_timeout_s: float = RELEASE_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._profiles = profiles
        self._modes = modes
        self._release_timeout_s = release_timeout_s
        self._clock = clock

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        operation = job.payload.get("operation")
        if operation == RELOAD:
            return await self._reload(job)
        if operation == CUSTOM_HASH:
            return await self._register(job.payload)
        logger.error("maintenance_operation_unknown", job_id=job.id)
        raise JobFailedError(INTERNAL, None, retryable=False)

    async def _reload(self, job: Job) -> JobOutcome:
        try:
            target = await to_thread.run_sync(self._profiles.reload_target)
        except ProfileUnavailableError as error:
            raise JobFailedError(MODEL_FILE_MISSING, error.detail, retryable=False) from None
        await self._ask("none", job.id)
        try:
            await self._wait_for_release()
        except JobFailedError:
            # Hand the GPU back to the text mode, so a reload that failed does not leave the queue without a runtime.
            with contextlib.suppress(JobFailedError):
                await self._ask("llm", job.id)
            raise
        await self._ask("llm", job.id)
        logger.info("model_reload_requested", job_id=job.id, state=target.profile_id)
        return JobDone({"profile_id": target.profile_id, "model_file": target.model_file})

    async def _ask(self, mode: DesiredMode, job_id: str) -> None:
        try:
            await to_thread.run_sync(self._modes.request_mode, mode, job_id)
        except ModeRefusedError:
            raise JobFailedError(ORCHESTRATOR_UNAVAILABLE, _DRAINING, retryable=False) from None
        except OrchestratorUnavailableError:
            raise JobFailedError(ORCHESTRATOR_UNAVAILABLE, _UNANSWERED, retryable=False) from None

    async def _wait_for_release(self) -> None:
        """Returns once the orchestrator reports IDLE with the desired mode `none`. Raises JobFailedError with
        GPU_RELEASE_UNVERIFIED for ERROR or after `release_timeout_s`, and with ORCHESTRATOR_UNAVAILABLE when the
        control API does not answer or starts draining."""
        deadline = self._clock() + self._release_timeout_s
        try:
            status = await to_thread.run_sync(self._modes.read_state)
            while not (status.state == "IDLE" and status.desired_mode == "none"):
                if status.state == "ERROR":
                    raise JobFailedError(GPU_RELEASE_UNVERIFIED, _UNRELEASED, retryable=False)
                if status.draining:
                    raise JobFailedError(ORCHESTRATOR_UNAVAILABLE, _DRAINING, retryable=False)
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise JobFailedError(GPU_RELEASE_UNVERIFIED, _UNRELEASED, retryable=False)
                status = await to_thread.run_sync(
                    self._modes.wait_for_state, status.seq or 0, min(remaining, MAX_STATE_WAIT_S)
                )
        except OrchestratorUnavailableError:
            raise JobFailedError(ORCHESTRATOR_UNAVAILABLE, _UNANSWERED, retryable=False) from None

    async def _register(self, payload: dict[str, Any]) -> JobOutcome:
        kind, name, template = payload.get("kind"), payload.get("file"), payload.get("template")
        if kind not in FOLDERS or not isinstance(name, str) or not (template is None or template in _TEMPLATES):
            raise JobFailedError(INTERNAL, None, retryable=False)
        checked_kind = cast(ModelKind, kind)
        checked_template = cast(TextTemplate | None, template)
        try:
            # abandon_on_cancel: a shutdown does not wait for a hash of many gigabytes. The job stays running, and the
            # next start ends it with JOB_INTERRUPTED (one attempt), so the user registers the file again.
            profile = await to_thread.run_sync(
                self._profiles.store_hashed, checked_kind, name, checked_template, abandon_on_cancel=True
            )
        except (ModelFileRefusedError, ModelFileMissingError) as error:
            raise JobFailedError(MODEL_FILE_MISSING, str(error), retryable=False) from None
        return JobDone(
            {"profile_id": profile.id, "kind": profile.kind, "bytes": profile.bytes, "sha256": profile.sha256}
        )
