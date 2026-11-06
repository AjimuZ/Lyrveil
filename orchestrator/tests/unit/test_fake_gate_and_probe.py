"""Tests of tests/fakes/fake_docker.py and tests/fakes/fake_nvml.py: the scripted state and failures of both ports."""

import pytest

from fakes.fake_docker import FakeContainerGate
from fakes.fake_nvml import FakeGpuProbe
from lyrveil_orchestrator.container_gate import (
    ContainerGate,
    ContainerMissingError,
    ContainerName,
    ContainerNotAllowedError,
    ContainerState,
    DockerResponseError,
    DockerUnavailableError,
    ForeignContainerError,
    HealthStatus,
    ProcessTable,
)
from lyrveil_orchestrator.gpu_monitor import CudaVersion, GpuProbeError, GPUResourceMonitor, VramReading

LLM = ContainerName.LLM
IMAGE = ContainerName.IMAGE


@pytest.fixture
def anyio_backend() -> str:
    """Runs the async tests on asyncio, the event loop of the orchestrator."""
    return "asyncio"


def test_the_fake_gate_is_a_container_gate_that_opens_no_socket() -> None:
    gate = FakeContainerGate()
    assert isinstance(gate, ContainerGate)
    assert not hasattr(gate, "_client")


@pytest.mark.anyio
async def test_a_container_starts_reports_health_and_stops() -> None:
    gate = FakeContainerGate()
    before = await gate.inspect(LLM)
    assert (before.state, before.running, before.exit_code) == (ContainerState.CREATED, False, None)
    assert await gate.start(LLM) is True
    assert await gate.start(LLM) is False
    running = await gate.inspect(LLM)
    assert (running.state, running.running, running.health) == (ContainerState.RUNNING, True, HealthStatus.HEALTHY)
    assert await gate.top(LLM) == ProcessTable(("PID", "CMD"), (("1", "runtime"),))
    assert await gate.stop(LLM, 20) is True
    assert await gate.stop(LLM, 20) is False
    stopped = await gate.inspect(LLM)
    assert (stopped.state, stopped.running, stopped.exit_code) == (ContainerState.EXITED, False, 0)
    assert await gate.top(LLM) == ProcessTable((), ())
    assert (await gate.inspect(IMAGE)).state is ContainerState.CREATED


@pytest.mark.anyio
async def test_a_starting_container_can_be_scripted_and_a_crash_leaves_an_exit_code_and_logs() -> None:
    gate = FakeContainerGate()
    gate.containers[IMAGE].health_after_start = HealthStatus.STARTING
    await gate.start(IMAGE)
    assert (await gate.inspect(IMAGE)).health is HealthStatus.STARTING
    gate.crash(IMAGE, exit_code=137, logs="torch.OutOfMemoryError: CUDA out of memory")
    crashed = await gate.inspect(IMAGE)
    assert (crashed.state, crashed.exit_code, crashed.health) == (ContainerState.EXITED, 137, None)
    assert "OutOfMemoryError" in await gate.logs(IMAGE)


@pytest.mark.anyio
async def test_queued_failures_hit_the_named_call_in_order_and_then_stop() -> None:
    gate = FakeContainerGate()
    gate.fail(LLM, "start", times=2)
    gate.fail(LLM, "start", DockerResponseError(LLM, "start", "unexpected status", 500))
    for expected in (DockerUnavailableError, DockerUnavailableError, DockerResponseError):
        with pytest.raises(expected):
            await gate.start(LLM)
    assert await gate.start(LLM) is True
    assert (await gate.inspect(IMAGE)).state is ContainerState.CREATED
    assert [call for call in gate.calls if call[0] == "start"] == [("start", LLM)] * 4


@pytest.mark.anyio
async def test_a_missing_or_foreign_container_is_refused_like_the_real_gate_does() -> None:
    gate = FakeContainerGate()
    gate.containers[LLM].exists = False
    gate.containers[IMAGE].foreign = True
    with pytest.raises(ContainerMissingError):
        await gate.inspect(LLM)
    with pytest.raises(ForeignContainerError):
        await gate.start(IMAGE)
    assert gate.containers[IMAGE].state is ContainerState.CREATED


@pytest.mark.anyio
async def test_a_name_outside_the_allowlist_and_a_bad_grace_period_are_refused() -> None:
    gate = FakeContainerGate()
    with pytest.raises(ContainerNotAllowedError):
        await gate.inspect("lyrveil-llm")  # type: ignore[arg-type]  # A plain string is the case under test.
    for grace in (0, 121, True):
        with pytest.raises(ValueError, match="grace period"):
            await gate.stop(LLM, grace)
    assert gate.calls == []


@pytest.mark.anyio
async def test_the_gate_closes_and_works_as_a_context_manager() -> None:
    async with FakeContainerGate() as gate:
        assert gate.closed is False
    assert gate.closed is True


def test_the_probe_reads_a_card_shared_with_other_programs() -> None:
    probe = FakeGpuProbe(total_mib=12000, external_mib=1500, runtime_mib=8000)
    assert probe.read_memory() == VramReading(12000, 9500, 2500)
    facts = probe.read_facts()
    assert (facts.device_name, facts.driver_version, facts.cuda_driver_version) == (
        "NVIDIA GeForce RTX 4070",
        "596.36",
        CudaVersion(13, 0),
    )
    probe.external_mib = 9000
    assert probe.read_memory() == VramReading(12000, 12000, 0)


def test_scripted_external_use_changes_between_reads_and_the_last_value_stays() -> None:
    probe = FakeGpuProbe(total_mib=10000, external_mib=1000)
    probe.script_external(2000, 6000)
    assert [probe.read_memory().used_mib for _ in range(4)] == [2000, 6000, 6000, 6000]


def test_failures_are_queued_or_permanent_and_every_attempt_is_counted() -> None:
    probe = FakeGpuProbe()
    probe.fail_next("nvmlInit failed: Driver Not Loaded", times=2)
    for _ in range(2):
        with pytest.raises(GpuProbeError, match="Driver Not Loaded"):
            probe.read_memory()
    assert probe.read_memory().total_mib == 12282
    probe.error = "nvmlDeviceGetHandleByIndex failed: Not Found"
    with pytest.raises(GpuProbeError, match="Not Found"):
        probe.read_facts()
    probe.error = None
    assert probe.read_facts().memory.free_mib > 0
    assert probe.reads == 5


def test_the_probe_works_under_the_resource_monitor_and_closes_more_than_once() -> None:
    probe = FakeGpuProbe()
    monitor = GPUResourceMonitor(probe)
    assert monitor.memory().total_mib == probe.total_mib
    monitor.close()
    monitor.close()
    assert probe.closes == 2
