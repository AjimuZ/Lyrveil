"""ModelLifecycleManager: owns the GPU state, the desired mode, the single lease and the event log.

It moves the runtimes toward the desired mode through the ProcessSupervisor, one transition of state_machine.py at a
time, and applies the lease rules of docs/gpu-orchestration.md (Leases and the global lock). Delivered in P02.2: the
text-mode path (start from the default mode `llm`, readiness, stop with release verification, drain), the leases,
and a reduced recovery. Delivered in P05.7: the switch to mode `image` and back takes the same load and stop path,
tested with the exclusivity rules, and recovery verifies the release before IDLE. Delivered in P06.1: the GPU side of
the 18 steps of an image request, with the job that asked for a mode named on the events of the switch it causes,
tested step by step (T-IMG-04) and by a property over random event sequences (T-ORC-02). Delivered in P06.2: the VRAM
check before a load, which holds the GPU in IDLE with `blocked_reason` GPU_INSUFFICIENT_VRAM while other programs hold
the memory, and checks again every few seconds (T-ORC-05). Delivered in P06.4: the whole recovery table (T-ORC-06),
including a runtime found still starting; a restart of the runtime after a lease released with `oom`; the record of
the last failed load in the state document, with `oom` for an allocation failure, from which the backend lowers the
launch level (T-ORC-08); the return to mode `llm` after three failed image loads in a row; and the timed retry out of
ERROR.

Everything runs on one asyncio event loop. The lease and mode methods never await, so each sees and changes the state
in one piece. `step` awaits container work, and before each await it enters a LOADING or UNLOADING state, in which no
lease can be granted, so no lease ever outlives a mode change (Exclusivity rule 4).
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from lyrveil_orchestrator.container_gate import ContainerStatus, HealthStatus
from lyrveil_orchestrator.state_machine import INITIAL_STATE, GpuState, Runtime, transition
from lyrveil_orchestrator.supervisor import (
    InsufficientVram,
    LoadFailedError,
    LoadReport,
    ProcessSupervisor,
    ReleaseReport,
    SupervisorError,
)

logger = logging.getLogger(__name__)

# docs/gpu-orchestration.md, Leases and the global lock.
DEFAULT_LEASE_TTL_S = 30
MAX_LEASE_TTL_S = 120
# docs/api.md, GET /v1/events: the newest events kept.
EVENT_BUFFER = 5000
# docs/gpu-orchestration.md, Load failures: a load is retried after 5 s and 20 s, and the third failure in a row ends
# in ERROR.
MAX_LOAD_FAILURES = 3
LOAD_RETRY_DELAYS_S = (5.0, 20.0)
# docs/gpu-orchestration.md, Load failures: ERROR retries every 300 s, at most 3 times an hour, or on the user's action.
ERROR_RETRY_S = 300.0
ERROR_RETRIES_PER_HOUR = 3
_HOUR_S = 3600.0
# ERROR codes that wait for the user's action alone: a missing container needs `.\setup.cmd` first (Recovery). Every
# other ERROR (Docker unreachable, three failed loads, a release not verified) retries on the timer, since its cause
# can pass by itself.
_WAITS_FOR_USER = frozenset({"RUNTIME_CONTAINER_MISSING"})
# How often a ready runtime's health is read, and the longest wait of the run loop between steps.
HEALTH_INTERVAL_S = 5.0
TICK_S = 1.0
# docs/api.md, job errors: the reason a load is held back in IDLE.
GPU_INSUFFICIENT_VRAM = "GPU_INSUFFICIENT_VRAM"


@dataclass(frozen=True)
class VramRequirements:
    """The free VRAM each runtime needs before its start, in MiB, and how often a held-back load checks again
    (docs/gpu-orchestration.md, Verifying GPU release).

    The text default is the smallest `vram_required_mib` of the catalog, 6000, so the check never holds back a load
    that one of the profiles could make; the backend does not send the active profile's own value yet (P06.6). The
    image default is `vram.required_image_mib`.
    """

    llm_mib: int = 6000
    image_mib: int = 6500
    recheck_s: float = 5.0

    def required_mib(self, runtime: Runtime) -> int:
        """Returns the free VRAM `runtime` needs before its start."""
        return self.llm_mib if runtime is Runtime.LLM else self.image_mib


class Mode(StrEnum):
    """The desired mode of PUT /v1/desired-mode. `none` keeps both runtimes stopped."""

    LLM = "llm"
    IMAGE = "image"
    NONE = "none"


class LeaseOutcome(StrEnum):
    """How the holder of a lease ended its work."""

    OK = "ok"
    OOM = "oom"
    RUNTIME_ERROR = "runtime_error"


_MODE_RUNTIME: Mapping[Mode, Runtime | None] = MappingProxyType(
    {Mode.LLM: Runtime.LLM, Mode.IMAGE: Runtime.IMAGE, Mode.NONE: None}
)
_LOADING = MappingProxyType({Runtime.LLM: GpuState.LOADING_LLM, Runtime.IMAGE: GpuState.LOADING_IMAGE})
_READY = MappingProxyType({Runtime.LLM: GpuState.LLM_READY, Runtime.IMAGE: GpuState.IMAGE_READY})
_GENERATING = MappingProxyType({Runtime.LLM: GpuState.GENERATING_TEXT, Runtime.IMAGE: GpuState.GENERATING_IMAGE})
_UNLOADING = MappingProxyType({Runtime.LLM: GpuState.UNLOADING_LLM, Runtime.IMAGE: GpuState.UNLOADING_IMAGE})
_OOM_CODE = MappingProxyType({Runtime.LLM: "LLM_OOM", Runtime.IMAGE: "IMAGE_OOM"})
# The health of a running runtime that recovery keeps: healthy, or still starting and possibly about to be.
_KEPT_HEALTH = frozenset({HealthStatus.HEALTHY, HealthStatus.STARTING})


@dataclass(frozen=True)
class Lease:
    """The global GPU lock. `expires_at` is on the manager's monotonic clock; a heartbeat moves it `ttl_s` ahead."""

    id: str
    mode: Mode
    job_id: str
    ttl_s: int
    expires_at: float


@dataclass(frozen=True)
class LifecycleEvent:
    """One transition, as GET /v1/events returns it. `code` is the failure that caused it, if any; `details` holds
    VRAM values in MiB and exit codes, never user data.

    A load held back for VRAM, and the end of that hold without a load, are recorded too, with `source` equal to
    `target` (IDLE), so `seq` moves and a long poll sees the change of `blocked_reason`.
    """

    seq: int
    at: str
    source: GpuState
    target: GpuState
    reason: str
    job_id: str | None
    code: str | None
    details: Mapping[str, int | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Returns the JSON form, with `from` and `to` as docs/gpu-orchestration.md names them."""
        return {
            "seq": self.seq,
            "at": self.at,
            "from": self.source.value,
            "to": self.target.value,
            "reason": self.reason,
            "job_id": self.job_id,
            "code": self.code,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class LoadFailure:
    """The last failed load since a runtime was last ready, as the state document's `load_failure` reports it.

    `seq` is the event of the failure, so a reader acts once on each. `oom` is True for an allocation failure of the
    text runtime, after which the backend lowers the launch level before the retry. `failures` counts the failed loads
    of `runtime` in a row, and `final` is True when no retry follows: the text runtime then waits in ERROR, and the
    image runtime gives the GPU back to mode `llm`, so the backend ends `job_id`, the job the mode was asked for.
    """

    seq: int
    runtime: Runtime
    code: str
    oom: bool
    failures: int
    final: bool
    job_id: str | None

    def to_dict(self) -> dict[str, Any]:
        """Returns the JSON form, with the runtime as `mode`."""
        return {
            "seq": self.seq,
            "mode": self.runtime.value,
            "code": self.code,
            "oom": self.oom,
            "failures": self.failures,
            "final": self.final,
            "job_id": self.job_id,
        }


class LeaseRefusedError(Exception):
    """No lease was granted. `code` is NOT_READY, when the state or the desired mode does not match, or LEASE_HELD."""

    def __init__(self, code: str) -> None:
        super().__init__(f"lease refused: {code}")
        self.code = code


class LeaseExpiredError(Exception):
    """The lease is not the current one: it expired, was released, was lost with the runtime or never existed."""

    def __init__(self) -> None:
        super().__init__("lease expired")
        self.code = "LEASE_EXPIRED"


class LifecycleConflictError(Exception):
    """The request does not fit the current state: a mode change while draining, or a recover outside ERROR."""


class ModelLifecycleManager:
    """Drives the GPU state machine with `supervisor`. The state starts as RECOVERING and the desired mode as `llm`.

    `run` performs the work; tests call `step` directly. `clock` is monotonic and drives lease expiry, retries and the
    VRAM checks. `vram` gives the free memory each runtime needs before its start.
    """

    def __init__(
        self,
        supervisor: ProcessSupervisor,
        clock: Callable[[], float] = time.monotonic,
        health_interval_s: float = HEALTH_INTERVAL_S,
        tick_s: float = TICK_S,
        vram: VramRequirements | None = None,
    ) -> None:
        self.boot_id = str(uuid.uuid4())
        self._supervisor = supervisor
        self._clock = clock
        self._health_interval_s = health_interval_s
        self._tick_s = tick_s
        self._vram = vram or VramRequirements()
        # The last check that held the load back, while the GPU waits in IDLE for other programs to free memory;
        # None otherwise. Only IDLE holds one: every other state clears it.
        self._blocked: InsufficientVram | None = None
        self._vram_check_at = 0.0
        self._state = INITIAL_STATE
        self._desired = Mode.LLM
        # The job on whose behalf the desired mode was last requested, None for the default mode or a request without
        # one. The transitions of the switch toward that mode name it.
        self._mode_job: str | None = None
        self._draining = False
        self._lease: Lease | None = None
        self._last_error: str | None = None
        self._seq = 0
        self._events: deque[LifecycleEvent] = deque(maxlen=EVENT_BUFFER)
        self._load_failures = 0
        self._load_failure: LoadFailure | None = None
        self._retry_at = 0.0
        # The runtime that recovery must stop even when it is healthy or starting: its lease ended with `oom`, so the
        # restart frees its memory and reads the launch file again, or its load just failed.
        self._restart: Runtime | None = None
        # When ERROR retries by itself, None when it waits for the user; and the times of the timed retries made, for
        # the hourly limit.
        self._error_retry_at: float | None = None
        self._timed_retries: deque[float] = deque()
        self._next_health_check = 0.0
        self._wake = asyncio.Event()
        self._changed = asyncio.Event()

    @property
    def state(self) -> GpuState:
        return self._state

    @property
    def desired_mode(self) -> Mode:
        return self._desired

    @property
    def draining(self) -> bool:
        return self._draining

    @property
    def lease(self) -> Lease | None:
        return self._lease

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def blocked(self) -> InsufficientVram | None:
        """The check that holds the load back in IDLE, or None when no load waits for VRAM."""
        return self._blocked

    @property
    def load_failure(self) -> LoadFailure | None:
        """The last failed load since a runtime was last ready, or None."""
        return self._load_failure

    @property
    def error_retry_at(self) -> float | None:
        """When ERROR moves to RECOVERING by itself, on the manager's clock; None outside ERROR or when it waits for
        the user."""
        return self._error_retry_at

    @property
    def seq(self) -> int:
        return self._seq

    # Leases. None of these await.

    def acquire(self, mode: Mode, job_id: str, ttl_s: int = DEFAULT_LEASE_TTL_S) -> Lease:
        """Grants the lease for `job_id` and enters GENERATING_TEXT or GENERATING_IMAGE.

        Raises LeaseRefusedError with LEASE_HELD while another lease exists, and with NOT_READY unless the state is the
        ready state of `mode` and the desired mode is still `mode` with no drain, so a pending mode change refuses new
        leases. Raises ValueError for mode `none` or a `ttl_s` outside 1 to MAX_LEASE_TTL_S.
        """
        runtime = _MODE_RUNTIME[mode]
        if runtime is None or not 1 <= ttl_s <= MAX_LEASE_TTL_S:
            raise ValueError("a lease needs mode llm or image and a ttl from 1 to 120 seconds")
        self._expire_overdue()
        if self._lease is not None:
            raise LeaseRefusedError("LEASE_HELD")
        if self._state is not _READY[runtime] or self._target_runtime() is not runtime:
            raise LeaseRefusedError("NOT_READY")
        lease = Lease(str(uuid.uuid4()), mode, job_id, ttl_s, self._clock() + ttl_s)
        self._lease = lease
        self._move(_GENERATING[runtime], "lease granted", job_id=job_id)
        return lease

    def heartbeat(self, lease_id: str) -> Lease:
        """Extends the current lease by its ttl and returns it. Raises LeaseExpiredError for any other lease id, and
        for the current lease once its ttl has passed, which ends it."""
        self._expire_overdue()
        lease = self._current(lease_id)
        renewed = replace(lease, expires_at=self._clock() + lease.ttl_s)
        self._lease = renewed
        return renewed

    def release(self, lease_id: str, outcome: LeaseOutcome) -> None:
        """Ends the current lease. `ok` returns to the ready state; `oom` and `runtime_error` go to RECOVERING with
        the matching code. After `oom` recovery restarts the runtime, healthy or not, so its memory is freed and the
        launch file read again (docs/gpu-orchestration.md, Load failures). Raises LeaseExpiredError, changing
        nothing, when `lease_id` is not the current lease."""
        self._expire_overdue()
        lease = self._current(lease_id)
        runtime = _MODE_RUNTIME[lease.mode]
        assert runtime is not None  # acquire grants no lease of mode none.
        self._lease = None
        if outcome is LeaseOutcome.OK:
            self._move(_READY[runtime], "lease released", job_id=lease.job_id)
        else:
            code = _OOM_CODE[runtime] if outcome is LeaseOutcome.OOM else "RUNTIME_UNAVAILABLE"
            if outcome is LeaseOutcome.OOM:
                self._restart = runtime
            self._move(GpuState.RECOVERING, f"lease released with {outcome}", job_id=lease.job_id, code=code)
        self._wake.set()

    # Mode, drain and recovery requests. None of these await; the run loop applies them.

    def request_mode(self, mode: Mode, job_id: str | None = None) -> None:
        """Records the desired mode, on behalf of `job_id` when given. It is applied once no lease is active, and the
        events of the switch it causes, from the stop of the current runtime to the ready state of the new one, carry
        `job_id`. Raises LifecycleConflictError, changing nothing, while draining, since a drain refuses new modes."""
        if self._draining:
            raise LifecycleConflictError("the orchestrator is draining")
        self._desired = mode
        self._mode_job = job_id
        self._wake.set()

    def drain(self) -> None:
        """Stops the runtime once the current lease ends, refuses new leases and modes until `resume`."""
        self._draining = True
        self._wake.set()

    def resume(self) -> None:
        """Leaves the drain. The desired mode recorded before it applies again."""
        self._draining = False
        self._wake.set()

    def recover(self) -> None:
        """Leaves ERROR for RECOVERING and forgets earlier load failures, so the next load has its full retries. The
        timed retries made in the last hour still count toward their limit.

        Raises LifecycleConflictError, changing nothing, in any other state.
        """
        if self._state is not GpuState.ERROR:
            raise LifecycleConflictError("the state is not ERROR")
        self._load_failures = 0
        self._retry_at = 0.0
        self._error_retry_at = None
        self._move(GpuState.RECOVERING, "user action")
        self._wake.set()

    # Reading.

    def lease_document(self, lease: Lease) -> dict[str, Any]:
        """Returns the JSON form of `lease`, with the seconds left instead of the monotonic deadline."""
        return {
            "id": lease.id,
            "mode": lease.mode.value,
            "job_id": lease.job_id,
            "ttl_s": lease.ttl_s,
            "expires_in_s": round(max(0.0, lease.expires_at - self._clock()), 1),
        }

    async def snapshot(self) -> dict[str, Any]:
        """Returns the state document of docs/api.md, with fresh VRAM and container readings. A reading that fails is
        null; it never raises for a Docker or NVML failure.

        While a load is held back, `blocked_reason` is GPU_INSUFFICIENT_VRAM and `blocked_vram` gives the mode and
        the numbers of the last check; both are null otherwise. `load_failure` is the last failed load since a
        runtime was last ready (LoadFailure), null otherwise.
        """
        self._expire_overdue()
        vram = await self._supervisor.read_memory()
        runtimes: dict[str, dict[str, Any] | None] = {}
        for runtime in Runtime:
            try:
                runtimes[runtime.value] = _runtime_document(await self._supervisor.inspect(runtime))
            except SupervisorError:
                runtimes[runtime.value] = None
        blocked = self._blocked
        return {
            "boot_id": self.boot_id,
            "seq": self._seq,
            "state": self._state.value,
            "desired_mode": self._desired.value,
            "draining": self._draining,
            "lease": None if self._lease is None else self.lease_document(self._lease),
            "blocked_reason": None if blocked is None else GPU_INSUFFICIENT_VRAM,
            "blocked_vram": None
            if blocked is None
            else {"mode": blocked.runtime.value, "free_mib": blocked.free_mib, "required_mib": blocked.required_mib},
            "vram": None if vram is None else asdict(vram),
            "runtimes": runtimes,
            "last_error": self._last_error,
            "load_failure": None if self._load_failure is None else self._load_failure.to_dict(),
        }

    async def wait_for_change(self, since: int, wait_s: float) -> None:
        """Returns once `seq` exceeds `since`, or after `wait_s` seconds, whichever comes first."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        while self._seq <= since:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                return

    def events(self, boot_id: str | None, after: int) -> list[dict[str, Any]]:
        """Returns the buffered events with `seq` above `after`, oldest first. For another `boot_id`, the sequence
        restarted with this process, so every buffered event is returned."""
        floor = after if boot_id == self.boot_id else 0
        return [event.to_dict() for event in self._events if event.seq > floor]

    # The work.

    async def run(self) -> None:
        """Steps until cancelled. A step that fails unexpectedly is logged and retried on the next tick."""
        while True:
            self._wake.clear()
            try:
                progressed = await self.step()
            except Exception:
                logger.exception("lifecycle step failed")
                progressed = False
            if progressed:
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._next_wait())

    async def step(self) -> bool:
        """Takes at most one action toward the desired mode. Returns True when the state changed or an event was
        recorded, False when there was nothing to do yet, so a caller can repeat it until it returns False."""
        if self._expire_overdue():
            return True
        state = self._state
        if state is GpuState.RECOVERING:
            await self._reconcile()
            return True
        if state is GpuState.IDLE:
            return await self._step_idle()
        if state is GpuState.ERROR:
            return self._retry_from_error()
        for runtime in Runtime:
            if state is _READY[runtime] and self._target_runtime() is not runtime:
                await self._unload(runtime, "drain" if self._draining else "mode changed")
                return True
            if state in (_READY[runtime], _GENERATING[runtime]):
                return await self._watch_health(runtime)
        # LOADING and UNLOADING states begin and end inside one step.
        return False

    def _retry_from_error(self) -> bool:
        """Leaves ERROR for RECOVERING once the timed retry is due and fewer than ERROR_RETRIES_PER_HOUR timed retries
        were made in the last hour; otherwise moves the retry to the moment the oldest of them is an hour old. Returns
        True when it left ERROR.

        The retry leaves one more load attempt: a load that fails again returns to ERROR, with the next retry
        ERROR_RETRY_S later.
        """
        due = self._error_retry_at
        now = self._clock()
        if due is None or now < due:
            return False
        while self._timed_retries and now - self._timed_retries[0] >= _HOUR_S:
            self._timed_retries.popleft()
        if len(self._timed_retries) >= ERROR_RETRIES_PER_HOUR:
            self._error_retry_at = self._timed_retries[0] + _HOUR_S
            return False
        self._timed_retries.append(now)
        self._error_retry_at = None
        self._load_failures = min(self._load_failures, MAX_LOAD_FAILURES - 1)
        self._retry_at = 0.0
        self._move(GpuState.RECOVERING, "timed retry")
        return True

    async def _step_idle(self) -> bool:
        """Loads the runtime of the desired mode once the retry delay has passed and the card has the free VRAM it
        needs. Otherwise holds the load back in IDLE and checks again after `recheck_s`. Other programs' memory is
        only read, never reclaimed.

        Records an event when a hold begins, for the mode it waits for, and when it ends because no runtime is wanted
        any more; a load that follows a hold ends it with its own transition.
        """
        target = self._target_runtime()
        if target is None:
            return self._end_hold("no runtime wanted")
        now = self._clock()
        held = self._blocked
        if now < self._retry_at or (held is not None and held.runtime is target and now < self._vram_check_at):
            return False
        shortfall = await self._supervisor.insufficient_vram(target, self._vram.required_mib(target))
        if self._target_runtime() is not target:
            # The desired mode changed while the card was read: the next step decides again.
            return True
        if shortfall is None:
            await self._load(target)
            return True
        self._blocked = shortfall
        self._vram_check_at = self._clock() + self._vram.recheck_s
        if held is not None and held.runtime is target:
            return False
        self._note(
            "not enough free VRAM",
            job_id=self._mode_job,
            code=GPU_INSUFFICIENT_VRAM,
            details={"free_mib": shortfall.free_mib, "required_mib": shortfall.required_mib},
        )
        return True

    def _end_hold(self, reason: str) -> bool:
        """Ends a hold for VRAM without a load and records it. Returns True when there was one."""
        if self._blocked is None:
            return False
        self._blocked = None
        self._note(reason)
        return True

    def _target_runtime(self) -> Runtime | None:
        return None if self._draining else _MODE_RUNTIME[self._desired]

    def _next_wait(self) -> float:
        if self._lease is None:
            return self._tick_s
        return max(0.0, min(self._tick_s, self._lease.expires_at - self._clock()))

    def _current(self, lease_id: str) -> Lease:
        if self._lease is None or self._lease.id != lease_id:
            raise LeaseExpiredError()
        return self._lease

    def _expire_overdue(self) -> bool:
        """Ends the lease once its ttl has passed and enters RECOVERING with LEASE_LOST. Returns True when it did."""
        lease = self._lease
        if lease is None or self._clock() < lease.expires_at:
            return False
        self._lease = None
        self._move(GpuState.RECOVERING, "lease expired", job_id=lease.job_id, code="LEASE_LOST")
        return True

    async def _load(self, runtime: Runtime) -> None:
        """Starts `runtime` and enters its ready state, or RECOVERING with the failure recorded in `load_failure`.

        Failures count in a row for one runtime: a load of the other runtime starts the count again. The retry waits
        LOAD_RETRY_DELAYS_S, during which the backend lowers the launch level after an allocation failure. The event of
        a failure carries `failures`, `oom` (1 or 0) and the exit code in its details.
        """
        job_id = self._mode_job
        if self._load_failure is not None and self._load_failure.runtime is not runtime:
            self._load_failures = 0
        self._move(_LOADING[runtime], f"mode {runtime}", job_id=job_id)
        try:
            report = await self._supervisor.start(runtime)
        except SupervisorError as error:
            self._load_failures += 1
            delay = LOAD_RETRY_DELAYS_S[min(self._load_failures, len(LOAD_RETRY_DELAYS_S)) - 1]
            self._retry_at = self._clock() + delay
            oom = isinstance(error, LoadFailedError) and error.oom
            exit_code = error.exit_code if isinstance(error, LoadFailedError) else None
            # A load that timed out leaves the container starting: recovery stops it rather than wait for it again.
            self._restart = runtime
            self._move(
                GpuState.RECOVERING,
                "failed or timed out",
                job_id=job_id,
                code=error.code,
                details={"failures": self._load_failures, "oom": int(oom), "exit_code": exit_code},
            )
            self._load_failure = LoadFailure(
                seq=self._seq,
                runtime=runtime,
                code=error.code,
                oom=oom,
                failures=self._load_failures,
                final=self._load_failures >= MAX_LOAD_FAILURES,
                job_id=job_id,
            )
            return
        self._load_failures = 0
        self._next_health_check = self._clock() + self._health_interval_s
        self._move(_READY[runtime], "healthy, model verified", job_id=job_id, details=_load_details(report))

    async def _unload(self, runtime: Runtime, reason: str) -> None:
        # A drain stops the runtime for no job; a mode change stops it for the job that asked for the new mode.
        job_id = None if self._draining else self._mode_job
        self._move(_UNLOADING[runtime], reason, job_id=job_id)
        try:
            report = await self._supervisor.stop(runtime)
        except SupervisorError as error:
            self._move(GpuState.RECOVERING, "stop or release failed", job_id=job_id, code=error.code)
            return
        self._move(GpuState.IDLE, "release verified", job_id=job_id, details=_release_details(report))

    async def _watch_health(self, runtime: Runtime) -> bool:
        """Reads the runtime's health every health interval and enters RECOVERING when it is lost, ending any lease."""
        if self._clock() < self._next_health_check:
            return False
        self._next_health_check = self._clock() + self._health_interval_s
        try:
            status = await self._supervisor.inspect(runtime)
        except SupervisorError as error:
            # One unanswered inspect is not proof that the runtime failed; the next tick asks again.
            logger.warning("health read of %s failed: %s", runtime, error.code)
            return False
        if _healthy(status) or self._state not in (_READY[runtime], _GENERATING[runtime]):
            return False
        lease, self._lease = self._lease, None
        self._move(
            GpuState.RECOVERING,
            "health lost",
            job_id=None if lease is None else lease.job_id,
            code="RUNTIME_UNAVAILABLE",
            details={"exit_code": status.exit_code},
        )
        return True

    async def _reconcile(self) -> None:
        """Applies the recovery table of docs/gpu-orchestration.md (Recovery) to what Docker reports, and ends in a
        stable state.

        - A healthy runtime with the other stopped is kept: its ready state. One still starting is kept once it turns
          healthy within its load timeout, and stopped like an unhealthy one otherwise.
        - Both running logs EXCLUSIVITY_VIOLATION and stops the image runtime; the text runtime is then kept as above.
        - An unhealthy runtime, one without a health check, the runtime whose lease ended with `oom` and the one whose
          load just failed are stopped.
        - A missing container (RUNTIME_CONTAINER_MISSING) or an unreachable Docker (DOCKER_UNAVAILABLE) ends in ERROR.
        - With nothing kept: MAX_LOAD_FAILURES failed text loads in a row end in ERROR; failed image loads give the GPU
          back to mode `llm`; otherwise IDLE.

        IDLE is entered only once the release is verified: by the stops made here, or else by the memory settling;
        memory that does not settle ends in ERROR with GPU_RELEASE_UNVERIFIED. ERROR retries on a timer unless it
        waits for the user (`_enter_error`).
        """
        restart, self._restart = self._restart, None
        try:
            statuses = {runtime: await self._supervisor.inspect(runtime) for runtime in Runtime}
        except SupervisorError as error:
            self._enter_error(error.code)
            return
        running = {runtime for runtime, status in statuses.items() if status.running}
        kept = {runtime for runtime in running if statuses[runtime].health in _KEPT_HEALTH}
        if running == set(Runtime):
            self._last_error = "EXCLUSIVITY_VIOLATION"
            logger.error("EXCLUSIVITY_VIOLATION: both runtimes were running")
            kept.discard(Runtime.IMAGE)
        if restart is not None:
            kept.discard(restart)
        stopped = running - kept
        if not await self._stop_each(stopped):
            return
        if kept:
            # At most one runtime is kept: with both running, the image runtime was dropped above.
            runtime = kept.pop()
            if statuses[runtime].health is HealthStatus.HEALTHY or await self._became_ready(runtime):
                self._next_health_check = self._clock() + self._health_interval_s
                self._move(_READY[runtime], f"{runtime} healthy")
                return
            if not await self._stop_each({runtime}):
                return
            stopped.add(runtime)
        failure = self._load_failure
        image_failed = failure is not None and failure.runtime is Runtime.IMAGE
        if self._load_failures >= MAX_LOAD_FAILURES and not image_failed:
            self._enter_error(failure.code if failure is not None else self._last_error)
            return
        if not stopped:
            # Exclusivity rule 2: IDLE only after a verified release. Each stop above verified its own; with nothing
            # stopped here, a runtime may have exited on its own or a stop may have timed out while the card still
            # held its memory, so the memory must settle before IDLE allows the next load.
            try:
                await self._supervisor.verify_settled()
            except SupervisorError as error:
                self._enter_error(error.code)
                return
        if self._load_failures >= MAX_LOAD_FAILURES:
            # docs/gpu-orchestration.md, Load failures: after the image's last retry the job fails and the mode returns
            # to text. The backend ends the job that `load_failure` names; its next image job asks for the mode again.
            self._load_failures = 0
            self._retry_at = 0.0
            self._desired = Mode.LLM
            self._mode_job = None
            self._move(GpuState.IDLE, "image load failed, mode llm")
            return
        self._move(GpuState.IDLE, "nothing running")

    async def _stop_each(self, runtimes: set[Runtime]) -> bool:
        """Stops `runtimes`, the image runtime first, each with its release verified. Returns False after entering
        ERROR when a stop fails."""
        for runtime in sorted(runtimes, key=lambda item: item is Runtime.LLM):
            try:
                await self._supervisor.stop(runtime)
            except SupervisorError as error:
                self._enter_error(error.code)
                return False
        return True

    async def _became_ready(self, runtime: Runtime) -> bool:
        """Waits for a runtime that recovery found still starting. Returns whether it turned healthy."""
        try:
            await self._supervisor.await_ready(runtime)
        except SupervisorError as error:
            logger.warning("%s found starting did not become ready: %s", runtime, error.code)
            return False
        return True

    def _enter_error(self, code: str | None) -> None:
        """Enters ERROR with `code` and schedules the timed retry ERROR_RETRY_S later, unless `code` waits for the
        user's action (`_WAITS_FOR_USER`)."""
        self._move(GpuState.ERROR, "not recoverable", code=code)
        self._error_retry_at = None if code in _WAITS_FOR_USER else self._clock() + ERROR_RETRY_S

    def _move(
        self,
        target: GpuState,
        reason: str,
        job_id: str | None = None,
        code: str | None = None,
        details: Mapping[str, int | None] | None = None,
    ) -> None:
        """Applies one transition of the table, records and logs its event and wakes long polls.

        Raises IllegalTransition, changing nothing, for a move the table does not hold. A ready state clears
        `last_error`; a failure code replaces it. Leaving IDLE ends any hold for VRAM. The ready state of the runtime
        whose load failed last clears `load_failure`; the other runtime's leaves it, so the backend still sees a final
        image failure once the text runtime is back. Leaving ERROR cancels its timed retry.
        """
        source = self._state
        self._state = transition(source, target)
        if target is not GpuState.IDLE:
            self._blocked = None
        if target is not GpuState.ERROR:
            self._error_retry_at = None
        if code is not None:
            self._last_error = code
        elif target in _READY.values():
            self._last_error = None
        failure = self._load_failure
        if failure is not None and target is _READY[failure.runtime]:
            self._load_failure = None
        self._record(source, target, reason, job_id, code, details)

    def _note(
        self,
        reason: str,
        job_id: str | None = None,
        code: str | None = None,
        details: Mapping[str, int | None] | None = None,
    ) -> None:
        """Records an event in the current state, without a transition: a hold for VRAM begins or ends. `last_error`
        is left alone, since `blocked_reason` reports the hold."""
        self._record(self._state, self._state, reason, job_id, code, details)

    def _record(
        self,
        source: GpuState,
        target: GpuState,
        reason: str,
        job_id: str | None,
        code: str | None,
        details: Mapping[str, int | None] | None,
    ) -> None:
        """Appends and logs the next event and wakes long polls."""
        self._seq += 1
        event = LifecycleEvent(
            seq=self._seq,
            at=datetime.now(UTC).isoformat(timespec="milliseconds"),
            source=source,
            target=target,
            reason=reason,
            job_id=job_id,
            code=code,
            details=dict(details or {}),
        )
        self._events.append(event)
        logger.info(json.dumps(event.to_dict()))
        self._changed.set()
        self._changed = asyncio.Event()


def _healthy(status: ContainerStatus) -> bool:
    return status.running and status.health is HealthStatus.HEALTHY


def _runtime_document(status: ContainerStatus) -> dict[str, Any]:
    return {
        "container": status.state.value,
        "health": None if status.health is None else status.health.value,
        "exit_code": status.exit_code,
    }


def _load_details(report: LoadReport) -> dict[str, int | None]:
    return {
        "used_before_mib": report.used_before_mib,
        "used_after_mib": report.used_after_mib,
        "footprint_mib": report.footprint_mib,
    }


def _release_details(report: ReleaseReport) -> dict[str, int | None]:
    return {
        "exit_code": report.exit_code,
        "used_before_stop_mib": report.used_before_stop_mib,
        "used_after_mib": report.settled.used_mib,
        "drop_mib": report.drop_mib,
        "footprint_mib": report.footprint_mib,
        "drop_shortfall_mib": report.drop_shortfall_mib,
    }
