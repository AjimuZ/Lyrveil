"""T-ORC-03: lease rules of ModelLifecycleManager, one lease at a time, mode match, expiry and heartbeat.

The manager runs on the fake gate and the fake probe with a clock the tests move, and `step` is called directly instead
of the run loop.
"""

import pytest

from fakes.fake_docker import FakeContainerGate
from fakes.fake_nvml import FakeGpuProbe
from lyrveil_orchestrator.container_gate import ContainerName, ContainerState, HealthStatus
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor
from lyrveil_orchestrator.lifecycle import (
    LeaseExpiredError,
    LeaseOutcome,
    LeaseRefusedError,
    LifecycleConflictError,
    Mode,
    ModelLifecycleManager,
)
from lyrveil_orchestrator.state_machine import GpuState, is_legal
from lyrveil_orchestrator.supervisor import ProcessSupervisor, SupervisorTimings

LLM = ContainerName.LLM
IMAGE = ContainerName.IMAGE
FAST = SupervisorTimings(readiness_poll_s=0.0, settle_interval_s=0.0)


@pytest.fixture
def anyio_backend() -> str:
    """Runs the async tests on asyncio, the event loop of the orchestrator."""
    return "asyncio"


class Clock:
    """A monotonic clock that moves only when a test advances it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def no_sleep(_: float) -> None:
    """Waits for nothing: the fake runtimes are ready at once and the fake memory never moves by itself."""


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def gate() -> FakeContainerGate:
    return FakeContainerGate()


@pytest.fixture
def manager(gate: FakeContainerGate, clock: Clock) -> ModelLifecycleManager:
    supervisor = ProcessSupervisor(gate, GPUResourceMonitor(FakeGpuProbe()), FAST, sleep=no_sleep, clock=clock)
    return ModelLifecycleManager(supervisor, clock=clock)


async def settle(manager: ModelLifecycleManager) -> GpuState:
    """Steps until nothing is left to do and returns the state reached."""
    for _ in range(20):
        if not await manager.step():
            return manager.state
    raise AssertionError("the manager kept changing state")


def transitions(manager: ModelLifecycleManager) -> list[tuple[str, str]]:
    return [(event["from"], event["to"]) for event in manager.events(None, 0)]


@pytest.mark.anyio
async def test_the_default_mode_loads_the_text_runtime_along_the_table(
    manager: ModelLifecycleManager, gate: FakeContainerGate
) -> None:
    assert (manager.state, manager.desired_mode) == (GpuState.RECOVERING, Mode.LLM)
    assert await settle(manager) is GpuState.LLM_READY
    assert transitions(manager) == [
        ("RECOVERING", "IDLE"),
        ("IDLE", "LOADING_LLM"),
        ("LOADING_LLM", "LLM_READY"),
    ]
    assert all(is_legal(GpuState(source), GpuState(target)) for source, target in transitions(manager))
    assert [call for call in gate.calls if call[0] == "start"] == [("start", LLM)]
    assert gate.containers[IMAGE].state is ContainerState.CREATED


@pytest.mark.anyio
async def test_one_lease_at_a_time(manager: ModelLifecycleManager) -> None:
    await settle(manager)
    lease = manager.acquire(Mode.LLM, "job-1")
    assert manager.state is GpuState.GENERATING_TEXT
    for mode in (Mode.LLM, Mode.IMAGE):
        with pytest.raises(LeaseRefusedError) as refused:
            manager.acquire(mode, "job-2")
        assert refused.value.code == "LEASE_HELD"
    manager.release(lease.id, LeaseOutcome.OK)
    assert (manager.state, manager.lease) == (GpuState.LLM_READY, None)
    assert manager.acquire(Mode.LLM, "job-2").job_id == "job-2"


@pytest.mark.anyio
async def test_a_lease_needs_the_ready_state_of_its_mode_and_no_pending_change(
    manager: ModelLifecycleManager,
) -> None:
    with pytest.raises(LeaseRefusedError, match="NOT_READY"):
        manager.acquire(Mode.LLM, "job-1")
    await settle(manager)
    with pytest.raises(LeaseRefusedError, match="NOT_READY"):
        manager.acquire(Mode.IMAGE, "job-1")
    manager.request_mode(Mode.NONE)
    with pytest.raises(LeaseRefusedError, match="NOT_READY"):
        manager.acquire(Mode.LLM, "job-1")
    manager.request_mode(Mode.LLM)
    manager.drain()
    with pytest.raises(LeaseRefusedError, match="NOT_READY"):
        manager.acquire(Mode.LLM, "job-1")
    with pytest.raises(ValueError, match="mode llm or image"):
        manager.acquire(Mode.NONE, "job-1")
    with pytest.raises(ValueError, match="ttl"):
        manager.acquire(Mode.LLM, "job-1", ttl_s=121)


@pytest.mark.anyio
async def test_heartbeats_keep_the_lease_and_a_missed_one_expires_it(
    manager: ModelLifecycleManager, clock: Clock
) -> None:
    await settle(manager)
    lease = manager.acquire(Mode.LLM, "job-1")
    for _ in range(3):
        clock.now += 20
        assert manager.heartbeat(lease.id).expires_at == clock.now + 30
    assert manager.state is GpuState.GENERATING_TEXT
    clock.now += 30
    with pytest.raises(LeaseExpiredError):
        manager.heartbeat(lease.id)
    assert (manager.state, manager.last_error, manager.lease) == (GpuState.RECOVERING, "LEASE_LOST", None)
    # The runtime is still healthy, so recovery returns to the ready state without a restart.
    assert await settle(manager) is GpuState.LLM_READY


@pytest.mark.anyio
async def test_the_run_loop_expires_a_lease_whose_holder_went_silent(
    manager: ModelLifecycleManager, clock: Clock
) -> None:
    await settle(manager)
    lease = manager.acquire(Mode.LLM, "job-1")
    clock.now += 29
    assert await manager.step() is False
    clock.now += 1
    assert await manager.step() is True
    assert manager.state is GpuState.RECOVERING
    assert manager.events(None, 0)[-1]["reason"] == "lease expired"
    assert manager.events(None, 0)[-1]["job_id"] == "job-1"
    with pytest.raises(LeaseExpiredError):
        manager.release(lease.id, LeaseOutcome.OK)


@pytest.mark.anyio
async def test_an_unknown_lease_id_changes_nothing(manager: ModelLifecycleManager) -> None:
    await settle(manager)
    manager.acquire(Mode.LLM, "job-1")
    with pytest.raises(LeaseExpiredError):
        manager.heartbeat("not-the-lease")
    with pytest.raises(LeaseExpiredError):
        manager.release("not-the-lease", LeaseOutcome.OK)
    assert manager.state is GpuState.GENERATING_TEXT


@pytest.mark.anyio
async def test_a_mode_change_waits_for_the_lease_and_then_stops_the_runtime(
    manager: ModelLifecycleManager, gate: FakeContainerGate
) -> None:
    await settle(manager)
    lease = manager.acquire(Mode.LLM, "job-1")
    manager.request_mode(Mode.NONE)
    assert await settle(manager) is GpuState.GENERATING_TEXT
    assert gate.containers[LLM].state is ContainerState.RUNNING
    manager.release(lease.id, LeaseOutcome.OK)
    assert await settle(manager) is GpuState.IDLE
    assert gate.containers[LLM].state is ContainerState.EXITED
    assert transitions(manager)[-3:] == [
        ("GENERATING_TEXT", "LLM_READY"),
        ("LLM_READY", "UNLOADING_LLM"),
        ("UNLOADING_LLM", "IDLE"),
    ]


@pytest.mark.anyio
async def test_an_oom_release_goes_through_recovery(manager: ModelLifecycleManager) -> None:
    await settle(manager)
    lease = manager.acquire(Mode.LLM, "job-1")
    manager.release(lease.id, LeaseOutcome.OOM)
    assert (manager.state, manager.last_error) == (GpuState.RECOVERING, "LLM_OOM")
    assert await settle(manager) is GpuState.LLM_READY
    assert manager.last_error is None


@pytest.mark.anyio
async def test_a_runtime_that_dies_under_a_lease_ends_the_lease_and_is_restarted(
    manager: ModelLifecycleManager, gate: FakeContainerGate, clock: Clock
) -> None:
    await settle(manager)
    lease = manager.acquire(Mode.LLM, "job-1")
    gate.crash(LLM, exit_code=137)
    clock.now += 5
    assert await manager.step() is True
    assert (manager.state, manager.last_error, manager.lease) == (GpuState.RECOVERING, "RUNTIME_UNAVAILABLE", None)
    with pytest.raises(LeaseExpiredError):
        manager.heartbeat(lease.id)
    assert await settle(manager) is GpuState.LLM_READY
    assert [call for call in gate.calls if call[0] == "start"] == [("start", LLM)] * 2


@pytest.mark.anyio
async def test_three_failed_loads_end_in_error_until_recover(
    manager: ModelLifecycleManager, gate: FakeContainerGate, clock: Clock
) -> None:
    gate.containers[LLM].health_after_start = None
    assert await settle(manager) is GpuState.IDLE
    assert manager.last_error == "LLM_LOAD_FAILED"
    clock.now += 5
    assert await settle(manager) is GpuState.IDLE
    clock.now += 20
    assert await settle(manager) is GpuState.ERROR
    assert manager.last_error == "LLM_LOAD_FAILED"
    clock.now += 3600
    assert await settle(manager) is GpuState.ERROR
    gate.containers[LLM].health_after_start = HealthStatus.HEALTHY
    manager.recover()
    with pytest.raises(LifecycleConflictError):
        manager.recover()
    assert await settle(manager) is GpuState.LLM_READY
    # Each failed start left a running container without a health check, and recovery stopped each one.
    assert [call for call in gate.calls if call[0] == "start"] == [("start", LLM)] * 4
    assert [call for call in gate.calls if call[0] == "stop"] == [("stop", LLM)] * 3


@pytest.mark.anyio
async def test_a_drain_refuses_new_modes_until_resume(manager: ModelLifecycleManager) -> None:
    await settle(manager)
    manager.drain()
    with pytest.raises(LifecycleConflictError):
        manager.request_mode(Mode.IMAGE)
    assert await settle(manager) is GpuState.IDLE
    manager.resume()
    assert await settle(manager) is GpuState.LLM_READY
