"""Tests of ProcessSupervisor with the fake gate and probe: exclusive start, readiness and release verification."""

from collections.abc import Callable

import pytest

from fakes.fake_docker import FakeContainerGate
from fakes.fake_nvml import FakeGpuProbe
from lyrveil_orchestrator.container_gate import ContainerName, ContainerState, HealthStatus
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor
from lyrveil_orchestrator.state_machine import Runtime
from lyrveil_orchestrator.supervisor import (
    ExclusivityError,
    LoadFailedError,
    ProcessSupervisor,
    ReleaseFailedError,
    SupervisorError,
    SupervisorTimings,
)

LLM = ContainerName.LLM
IMAGE = ContainerName.IMAGE
FAST = SupervisorTimings(readiness_poll_s=2.0, settle_interval_s=0.5)
RUNTIME_MIB = 8000


@pytest.fixture
def anyio_backend() -> str:
    """Runs the async tests on asyncio, the event loop of the orchestrator."""
    return "asyncio"


class LoadingGate(FakeContainerGate):
    """A fake gate whose LLM runtime takes RUNTIME_MIB of the fake card while it runs."""

    def __init__(self, probe: FakeGpuProbe) -> None:
        super().__init__()
        self.probe = probe

    async def start(self, name: ContainerName) -> bool:
        started = await super().start(name)
        self.probe.runtime_mib = RUNTIME_MIB
        return started

    async def stop(self, name: ContainerName, grace_s: int) -> bool:
        stopped = await super().stop(name, grace_s)
        self.probe.runtime_mib = 0
        return stopped


class Harness:
    """A supervisor whose sleeps move a fake clock and may run a scripted action once."""

    def __init__(self) -> None:
        self.probe = FakeGpuProbe(external_mib=1500)
        self.gate = LoadingGate(self.probe)
        self.now = 0.0
        self.sleeps: list[float] = []
        self.on_sleep: list[Callable[[], None]] = []
        self.supervisor = ProcessSupervisor(
            self.gate, GPUResourceMonitor(self.probe), FAST, sleep=self.sleep, clock=lambda: self.now
        )

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep.pop(0)()


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.mark.anyio
async def test_a_start_is_refused_while_the_other_runtime_runs(harness: Harness) -> None:
    harness.gate.containers[IMAGE].state = ContainerState.RUNNING
    with pytest.raises(ExclusivityError) as refused:
        await harness.supervisor.start(Runtime.LLM)
    assert refused.value.code == "EXCLUSIVITY_VIOLATION"
    assert harness.gate.calls == [("inspect", IMAGE)]


@pytest.mark.anyio
async def test_a_start_waits_for_health_and_measures_the_footprint(harness: Harness) -> None:
    llm = harness.gate.containers[LLM]
    llm.health_after_start = HealthStatus.STARTING

    def turn_healthy() -> None:
        llm.health = HealthStatus.HEALTHY

    harness.on_sleep = [lambda: None, turn_healthy]
    report = await harness.supervisor.start(Runtime.LLM)
    assert harness.sleeps == [2.0, 2.0]
    assert (report.used_before_mib, report.used_after_mib, report.footprint_mib) == (1500, 9500, RUNTIME_MIB)


@pytest.mark.parametrize(("exit_code", "code"), [(78, "RUNTIME_CONFIG_INVALID"), (3, "LLM_LOAD_FAILED")])
@pytest.mark.anyio
async def test_a_runtime_that_exits_while_loading_fails_with_its_code(
    harness: Harness, exit_code: int, code: str
) -> None:
    harness.gate.containers[LLM].health_after_start = HealthStatus.STARTING
    harness.on_sleep = [lambda: harness.gate.crash(LLM, exit_code=exit_code)]
    with pytest.raises(LoadFailedError) as failed:
        await harness.supervisor.start(Runtime.LLM)
    assert (failed.value.code, failed.value.exit_code) == (code, exit_code)


@pytest.mark.anyio
async def test_a_runtime_that_never_turns_healthy_times_out(harness: Harness) -> None:
    harness.gate.containers[LLM].health_after_start = HealthStatus.STARTING
    with pytest.raises(LoadFailedError, match="timed out") as failed:
        await harness.supervisor.start(Runtime.LLM)
    assert failed.value.code == "LLM_LOAD_FAILED"
    assert harness.now == 240.0


@pytest.mark.anyio
async def test_a_missing_container_or_docker_down_maps_to_the_recovery_codes(harness: Harness) -> None:
    harness.gate.containers[LLM].exists = False
    with pytest.raises(SupervisorError) as missing:
        await harness.supervisor.start(Runtime.LLM)
    assert missing.value.code == "RUNTIME_CONTAINER_MISSING"
    harness.gate.fail(IMAGE, "inspect")
    with pytest.raises(SupervisorError) as down:
        await harness.supervisor.start(Runtime.LLM)
    assert down.value.code == "DOCKER_UNAVAILABLE"


@pytest.mark.anyio
async def test_a_stop_verifies_the_release_once_memory_settles(harness: Harness) -> None:
    await harness.supervisor.start(Runtime.LLM)
    # After the stop the card reads 1500 + noise: three samples within 64 MiB are needed.
    harness.probe.script_external(1500, 1500, 2100, 1540, 1520, 1500)
    report = await harness.supervisor.stop(Runtime.LLM)
    assert harness.gate.containers[LLM].state is ContainerState.EXITED
    assert [call[0] for call in harness.gate.calls[-3:]] == ["stop", "inspect", "top"]
    assert (report.exit_code, report.used_before_stop_mib, report.settled.used_mib) == (0, 9500, 1500)
    assert (report.drop_mib, report.footprint_mib) == (8000, RUNTIME_MIB)
    assert harness.sleeps == [0.5, 0.5, 0.5, 0.5]


@pytest.mark.anyio
async def test_memory_that_never_settles_fails_the_release(harness: Harness) -> None:
    await harness.supervisor.start(Runtime.LLM)
    harness.probe.script_external(*([1500, 3000] * 200))
    with pytest.raises(ReleaseFailedError, match="did not settle") as failed:
        await harness.supervisor.stop(Runtime.LLM)
    assert failed.value.code == "GPU_RELEASE_UNVERIFIED"
    assert harness.now == 60.0


@pytest.mark.anyio
async def test_unreadable_nvml_fails_the_release(harness: Harness) -> None:
    await harness.supervisor.start(Runtime.LLM)
    harness.probe.error = "nvmlDeviceGetMemoryInfo failed: Unknown Error"
    with pytest.raises(ReleaseFailedError, match="NVML"):
        await harness.supervisor.stop(Runtime.LLM)


@pytest.mark.anyio
async def test_a_container_that_does_not_end_fails_the_release(harness: Harness) -> None:
    class StuckGate(LoadingGate):
        async def stop(self, name: ContainerName, grace_s: int) -> bool:
            self.calls.append(("stop", name))
            return True

    harness.gate = StuckGate(harness.probe)
    supervisor = ProcessSupervisor(harness.gate, GPUResourceMonitor(harness.probe), FAST, sleep=harness.sleep)
    await supervisor.start(Runtime.LLM)
    with pytest.raises(ReleaseFailedError, match="did not end"):
        await supervisor.stop(Runtime.LLM)
