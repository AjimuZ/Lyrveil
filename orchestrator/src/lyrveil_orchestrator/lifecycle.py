"""ModelLifecycleManager: owns the GPU state, the desired mode, the single lease and the event log.

It moves the runtimes toward the desired mode through the ProcessSupervisor, one transition of state_machine.py at a
time, and applies the lease rules of docs/gpu-orchestration.md (Leases and the global lock). Delivered in P02.2: the
text-mode path (start from the default mode `llm`, readiness, stop with release verification, drain), the leases,
and a reduced recovery. Delivered in P05.7: the switch to mode `image` and back takes the same load and stop path,
tested with the exclusivity rules, and recovery verifies the release before IDLE. Not delivered yet (P06): the VRAM
check before a load with `blocked_reason`, the timed retry out of ERROR, launch-level fallbacks and the rest of the
image flow of the 18 steps.

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
from lyrveil_orchestrator.supervisor import LoadReport, ProcessSupervisor, ReleaseReport, SupervisorError

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
# How often a ready runtime's health is read, and the longest wait of the run loop between steps.
HEALTH_INTERVAL_S = 5.0
TICK_S = 1.0


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
    VRAM values in MiB and exit codes, never user data."""

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

    `run` performs the work; tests call `step` directly. `clock` is monotonic and drives lease expiry and retries.
    """

    def __init__(
        self,
        supervisor: ProcessSupervisor,
        clock: Callable[[], float] = time.monotonic,
        health_interval_s: float = HEALTH_INTERVAL_S,
        tick_s: float = TICK_S,
    ) -> None:
        self.boot_id = str(uuid.uuid4())
        self._supervisor = supervisor
        self._clock = clock
        self._health_interval_s = health_interval_s
        self._tick_s = tick_s
        self._state = INITIAL_STATE
        self._desired = Mode.LLM
        self._draining = False
        self._lease: Lease | None = None
        self._last_error: str | None = None
        self._seq = 0
        self._events: deque[LifecycleEvent] = deque(maxlen=EVENT_BUFFER)
        self._load_failures = 0
        self._retry_at = 0.0
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
        the matching code. Raises LeaseExpiredError, changing nothing, when `lease_id` is not the current lease."""
        self._expire_overdue()
        lease = self._current(lease_id)
        runtime = _MODE_RUNTIME[lease.mode]
        assert runtime is not None  # acquire grants no lease of mode none.
        self._lease = None
        if outcome is LeaseOutcome.OK:
            self._move(_READY[runtime], "lease released", job_id=lease.job_id)
        else:
            code = _OOM_CODE[runtime] if outcome is LeaseOutcome.OOM else "RUNTIME_UNAVAILABLE"
            self._move(GpuState.RECOVERING, f"lease released with {outcome}", job_id=lease.job_id, code=code)
        self._wake.set()

    # Mode, drain and recovery requests. None of these await; the run loop applies them.

    def request_mode(self, mode: Mode) -> None:
        """Records the desired mode. It is applied once no lease is active. Raises LifecycleConflictError while
        draining, since a drain refuses new modes."""
        if self._draining:
            raise LifecycleConflictError("the orchestrator is draining")
        self._desired = mode
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
        """Leaves ERROR for RECOVERING and forgets earlier load failures.

        Raises LifecycleConflictError, changing nothing, in any other state.
        """
        if self._state is not GpuState.ERROR:
            raise LifecycleConflictError("the state is not ERROR")
        self._load_failures = 0
        self._retry_at = 0.0
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
        null; it never raises for a Docker or NVML failure."""
        self._expire_overdue()
        vram = await self._supervisor.read_memory()
        runtimes: dict[str, dict[str, Any] | None] = {}
        for runtime in Runtime:
            try:
                runtimes[runtime.value] = _runtime_document(await self._supervisor.inspect(runtime))
            except SupervisorError:
                runtimes[runtime.value] = None
        return {
            "boot_id": self.boot_id,
            "seq": self._seq,
            "state": self._state.value,
            "desired_mode": self._desired.value,
            "draining": self._draining,
            "lease": None if self._lease is None else self.lease_document(self._lease),
            "blocked_reason": None,
            "vram": None if vram is None else asdict(vram),
            "runtimes": runtimes,
            "last_error": self._last_error,
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
        """Takes at most one action toward the desired mode. Returns True when the state changed, False when there
        was nothing to do yet, so a caller can repeat it until it returns False."""
        if self._expire_overdue():
            return True
        state = self._state
        if state is GpuState.RECOVERING:
            await self._reconcile()
            return True
        if state is GpuState.IDLE:
            target = self._target_runtime()
            if target is None or self._clock() < self._retry_at:
                return False
            await self._load(target)
            return True
        for runtime in Runtime:
            if state is _READY[runtime] and self._target_runtime() is not runtime:
                await self._unload(runtime, "drain" if self._draining else "mode changed")
                return True
            if state in (_READY[runtime], _GENERATING[runtime]):
                return await self._watch_health(runtime)
        # ERROR waits for recover. LOADING and UNLOADING states begin and end inside one step.
        return False

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
        self._move(_LOADING[runtime], f"mode {runtime}")
        try:
            report = await self._supervisor.start(runtime)
        except SupervisorError as error:
            self._load_failures += 1
            delay = LOAD_RETRY_DELAYS_S[min(self._load_failures, len(LOAD_RETRY_DELAYS_S)) - 1]
            self._retry_at = self._clock() + delay
            self._move(GpuState.RECOVERING, "failed or timed out", code=error.code)
            return
        self._load_failures = 0
        self._next_health_check = self._clock() + self._health_interval_s
        self._move(_READY[runtime], "healthy, model verified", details=_load_details(report))

    async def _unload(self, runtime: Runtime, reason: str) -> None:
        self._move(_UNLOADING[runtime], reason)
        try:
            report = await self._supervisor.stop(runtime)
        except SupervisorError as error:
            self._move(GpuState.RECOVERING, "stop or release failed", code=error.code)
            return
        self._move(GpuState.IDLE, "release verified", details=_release_details(report))

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
        """The reduced recovery table: keep one healthy runtime, stop anything else, and end in a stable state.

        Both running stops the image runtime with EXCLUSIVITY_VIOLATION. A missing container or an unreachable Docker
        ends in ERROR, and so does a state with nothing running after MAX_LOAD_FAILURES failed loads in a row. IDLE
        is entered only once the release is verified: by the stops made here, or else by the memory settling; memory
        that does not settle ends in ERROR with GPU_RELEASE_UNVERIFIED, which only `recover` leaves until P06.
        """
        try:
            statuses = {runtime: await self._supervisor.inspect(runtime) for runtime in Runtime}
        except SupervisorError as error:
            self._move(GpuState.ERROR, "not recoverable", code=error.code)
            return
        running = {runtime for runtime, status in statuses.items() if status.running}
        healthy = {runtime for runtime in running if _healthy(statuses[runtime])}
        to_stop = running - healthy
        if running == set(Runtime):
            self._last_error = "EXCLUSIVITY_VIOLATION"
            logger.error("EXCLUSIVITY_VIOLATION: both runtimes were running")
            to_stop.add(Runtime.IMAGE)
        for runtime in sorted(to_stop, key=lambda item: item is Runtime.LLM):
            try:
                await self._supervisor.stop(runtime)
            except SupervisorError as error:
                self._move(GpuState.ERROR, "not recoverable", code=error.code)
                return
        kept = healthy - to_stop
        if kept:
            runtime = kept.pop()
            self._next_health_check = self._clock() + self._health_interval_s
            self._move(_READY[runtime], f"{runtime} healthy")
            return
        if self._load_failures >= MAX_LOAD_FAILURES:
            self._move(GpuState.ERROR, "not recoverable", code=self._last_error)
            return
        if not to_stop:
            # Exclusivity rule 2: IDLE only after a verified release. Each stop above verified its own; with nothing
            # stopped here, a runtime may have exited on its own or a stop may have timed out while the card still
            # held its memory, so the memory must settle before IDLE allows the next load.
            try:
                await self._supervisor.verify_settled()
            except SupervisorError as error:
                self._move(GpuState.ERROR, "not recoverable", code=error.code)
                return
        self._move(GpuState.IDLE, "nothing running")

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
        `last_error`; a failure code replaces it.
        """
        source = self._state
        self._state = transition(source, target)
        if code is not None:
            self._last_error = code
        elif target in _READY.values():
            self._last_error = None
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
    }
