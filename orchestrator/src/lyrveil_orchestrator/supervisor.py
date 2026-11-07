"""ProcessSupervisor: starts a runtime container, waits until it is ready, stops it and verifies the GPU release.

It follows docs/gpu-orchestration.md (Exclusivity rules, Verifying GPU release) and acts only through ContainerGate
and GPUResourceMonitor. It holds no GPU state: the lifecycle manager decides when to call it and moves the state
machine around each call. Every failure is a SupervisorError whose `code` is an operational or job code of
docs/api.md, so the manager can report it as `last_error` without reading the message.

Not delivered yet (P06): the VRAM check before a load and the classification of load failures from the logs.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from lyrveil_orchestrator.container_gate import (
    ContainerGate,
    ContainerGateError,
    ContainerMissingError,
    ContainerName,
    ContainerStatus,
    ForeignContainerError,
    HealthStatus,
)
from lyrveil_orchestrator.gpu_monitor import GpuProbeError, GPUResourceMonitor, VramReading
from lyrveil_orchestrator.state_machine import Runtime

# The container of each runtime, and the one that must not run while it starts.
CONTAINERS: Mapping[Runtime, ContainerName] = MappingProxyType(
    {Runtime.LLM: ContainerName.LLM, Runtime.IMAGE: ContainerName.IMAGE}
)
_OTHER: Mapping[Runtime, Runtime] = MappingProxyType({Runtime.LLM: Runtime.IMAGE, Runtime.IMAGE: Runtime.LLM})
# The exit status of a runtime that refuses its launch file (docs/api.md, RUNTIME_CONFIG_INVALID).
CONFIG_INVALID_EXIT = 78
_LOAD_FAILED: Mapping[Runtime, str] = MappingProxyType(
    {Runtime.LLM: "LLM_LOAD_FAILED", Runtime.IMAGE: "IMAGE_LOAD_FAILED"}
)


@dataclass(frozen=True)
class SupervisorTimings:
    """The waits of docs/gpu-orchestration.md (Timeouts, Verifying GPU release). Tests shorten the intervals.

    A memory reading has settled when `settle_samples` consecutive samples, `settle_interval_s` apart, lie within
    `settle_tolerance_mib` of each other.
    """

    llm_load_timeout_s: float = 240.0
    image_load_timeout_s: float = 180.0
    llm_stop_grace_s: int = 20
    image_stop_grace_s: int = 30
    release_timeout_s: float = 60.0
    readiness_poll_s: float = 2.0
    settle_interval_s: float = 0.5
    settle_samples: int = 3
    settle_tolerance_mib: int = 64

    def load_timeout_s(self, runtime: Runtime) -> float:
        """Returns how long `runtime` may take to become healthy after its start."""
        return self.llm_load_timeout_s if runtime is Runtime.LLM else self.image_load_timeout_s

    def stop_grace_s(self, runtime: Runtime) -> int:
        """Returns the seconds between SIGTERM and SIGKILL when `runtime` stops."""
        return self.llm_stop_grace_s if runtime is Runtime.LLM else self.image_stop_grace_s


@dataclass(frozen=True)
class LoadReport:
    """What a successful start measured. Every value is None when NVML could not be read at that moment.

    `footprint_mib` is the growth of used memory over the load: informational, since other programs share the card.
    """

    runtime: Runtime
    used_before_mib: int | None
    used_after_mib: int | None
    footprint_mib: int | None


@dataclass(frozen=True)
class ReleaseReport:
    """What a verified release measured.

    `drop_mib` is used memory before the stop minus the settled value, None when the first reading failed.
    `footprint_mib` is the one measured at the last load of this supervisor, None when unknown. A drop smaller than
    the footprint is informational only: an external program may have taken memory meanwhile.
    """

    runtime: Runtime
    exit_code: int
    used_before_stop_mib: int | None
    settled: VramReading
    drop_mib: int | None
    footprint_mib: int | None


class SupervisorError(Exception):
    """A start or a stop that did not succeed. `code` is the docs/api.md code; the message holds no Docker value."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ExclusivityError(SupervisorError):
    """The other runtime was running, so the start was refused before any start call."""

    def __init__(self, runtime: Runtime) -> None:
        super().__init__("EXCLUSIVITY_VIOLATION", f"refused to start {runtime}: the other runtime is running")


class LoadFailedError(SupervisorError):
    """The runtime started but did not become healthy. `exit_code` is set when the container exited."""

    def __init__(self, runtime: Runtime, detail: str, exit_code: int | None = None) -> None:
        code = "RUNTIME_CONFIG_INVALID" if exit_code == CONFIG_INVALID_EXIT else _LOAD_FAILED[runtime]
        super().__init__(code, f"{runtime} did not become ready: {detail}")
        self.exit_code = exit_code


class ReleaseFailedError(SupervisorError):
    """The container did not end, or the GPU memory could not be seen to settle, within the limits."""

    def __init__(self, runtime: Runtime, detail: str) -> None:
        super().__init__("GPU_RELEASE_UNVERIFIED", f"release of {runtime} not verified: {detail}")


def _gate_failure(error: ContainerGateError) -> SupervisorError:
    """Maps a gate error to the recovery table's code: a missing or foreign container, or Docker unreachable."""
    if isinstance(error, ContainerMissingError | ForeignContainerError):
        return SupervisorError("RUNTIME_CONTAINER_MISSING", str(error))
    return SupervisorError("DOCKER_UNAVAILABLE", str(error))


class ProcessSupervisor:
    """Starts and stops the two runtimes through `gate`, and reads the card through `monitor`.

    The caller owns `gate` and `monitor`. `sleep` and `clock` exist for tests. Calls must not overlap for the same
    runtime; the lifecycle manager makes them one at a time.
    """

    def __init__(
        self,
        gate: ContainerGate,
        monitor: GPUResourceMonitor,
        timings: SupervisorTimings | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gate = gate
        self._monitor = monitor
        self._timings = timings or SupervisorTimings()
        self._sleep = sleep
        self._clock = clock
        self._footprints: dict[Runtime, int | None] = {}

    async def inspect(self, runtime: Runtime) -> ContainerStatus:
        """Returns the container's status. Raises SupervisorError with RUNTIME_CONTAINER_MISSING or
        DOCKER_UNAVAILABLE when the gate fails."""
        try:
            return await self._gate.inspect(CONTAINERS[runtime])
        except ContainerGateError as error:
            raise _gate_failure(error) from error

    async def read_memory(self) -> VramReading | None:
        """Returns a fresh memory reading of the card, or None when NVML fails. Reads in a worker thread."""
        try:
            return await asyncio.to_thread(self._monitor.memory)
        except GpuProbeError:
            return None

    async def start(self, runtime: Runtime) -> LoadReport:
        """Starts `runtime` and returns once Docker reports it healthy, which for KoboldCpp means the model is loaded.

        Raises ExclusivityError, without starting anything, when the other runtime runs (Exclusivity rule 3);
        LoadFailedError when the container exits, turns unhealthy, has no health check or is not healthy within the
        load timeout; and SupervisorError for a gate failure. On failure the container may still run: the caller's
        recovery stops it.
        """
        try:
            if (await self._gate.inspect(CONTAINERS[_OTHER[runtime]])).running:
                raise ExclusivityError(runtime)
            before = await self.read_memory()
            await self._gate.start(CONTAINERS[runtime])
            await self._wait_ready(runtime)
        except ContainerGateError as error:
            raise _gate_failure(error) from error
        after = await self.read_memory()
        footprint = after.used_mib - before.used_mib if before is not None and after is not None else None
        self._footprints[runtime] = footprint
        return LoadReport(
            runtime=runtime,
            used_before_mib=None if before is None else before.used_mib,
            used_after_mib=None if after is None else after.used_mib,
            footprint_mib=footprint,
        )

    async def stop(self, runtime: Runtime) -> ReleaseReport:
        """Stops `runtime` and returns once its release is verified (docs/gpu-orchestration.md, steps 1 to 3).

        Docker sends SIGKILL when the grace period ends, so a stop that returns leaves the container ended. Raises
        ReleaseFailedError when it still runs, still lists processes, or the memory does not settle within the
        release timeout or cannot be read; and SupervisorError for a gate failure. A container that was not running
        is verified the same way.
        """
        name = CONTAINERS[runtime]
        before = await self.read_memory()
        try:
            await self._gate.stop(name, self._timings.stop_grace_s(runtime))
            status = await self._gate.inspect(name)
            exit_code = status.exit_code
            if status.running or exit_code is None:
                raise ReleaseFailedError(runtime, "the container did not end")
            if (await self._gate.top(name)).processes:
                raise ReleaseFailedError(runtime, "the container still lists processes")
        except ContainerGateError as error:
            raise _gate_failure(error) from error
        settled = await self._settled_memory(runtime)
        return ReleaseReport(
            runtime=runtime,
            exit_code=exit_code,
            used_before_stop_mib=None if before is None else before.used_mib,
            settled=settled,
            drop_mib=None if before is None else before.used_mib - settled.used_mib,
            footprint_mib=self._footprints.get(runtime),
        )

    async def _wait_ready(self, runtime: Runtime) -> None:
        """Polls the container until it is healthy. Raises LoadFailedError as `start` describes."""
        name = CONTAINERS[runtime]
        deadline = self._clock() + self._timings.load_timeout_s(runtime)
        while True:
            status = await self._gate.inspect(name)
            if not status.running:
                raise LoadFailedError(runtime, "the container exited", status.exit_code)
            if status.health is HealthStatus.HEALTHY:
                return
            if status.health is None:
                # Without a health check nothing proves that the model loaded.
                raise LoadFailedError(runtime, "the container has no health check")
            if status.health is HealthStatus.UNHEALTHY:
                raise LoadFailedError(runtime, "the health check failed")
            if self._clock() >= deadline:
                raise LoadFailedError(runtime, "timed out")
            await self._sleep(self._timings.readiness_poll_s)

    async def _settled_memory(self, runtime: Runtime) -> VramReading:
        """Samples used memory until it settles and returns the last sample. Raises ReleaseFailedError when NVML
        fails or the memory keeps moving past the release timeout."""
        timings = self._timings
        deadline = self._clock() + timings.release_timeout_s
        recent: list[int] = []
        while True:
            try:
                reading = await asyncio.to_thread(self._monitor.memory)
            except GpuProbeError as error:
                raise ReleaseFailedError(runtime, "NVML could not be read") from error
            recent = [*recent, reading.used_mib][-timings.settle_samples :]
            if len(recent) == timings.settle_samples and max(recent) - min(recent) <= timings.settle_tolerance_mib:
                return reading
            if self._clock() >= deadline:
                raise ReleaseFailedError(runtime, "the GPU memory did not settle")
            await self._sleep(timings.settle_interval_s)
