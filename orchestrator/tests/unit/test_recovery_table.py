"""T-ORC-06: each row of the recovery table of docs/gpu-orchestration.md (Recovery), and the timed retry out of ERROR.
Also the orchestrator side of T-ORC-08 (Load failures and launch levels): the classification of a failed text load,
the `load_failure` the backend reads to lower the launch level, and the return to text after failed image loads.

The manager starts in RECOVERING, as at process start, over the fake gate and the fake probe. The containers are set
before the first step, as the orchestrator would find them. The supervisor's clock moves only with its own sleeps, so
a runtime that never turns healthy ends its wait at once; the manager's clock moves only when a test advances it.
"""

import logging
from collections.abc import Callable

import pytest

from fakes.fake_docker import FakeContainerGate
from fakes.fake_nvml import FakeGpuProbe
from lyrveil_orchestrator.container_gate import ContainerName, ContainerState, ContainerStatus, HealthStatus
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor
from lyrveil_orchestrator.lifecycle import (
    ERROR_RETRY_S,
    LeaseOutcome,
    Mode,
    ModelLifecycleManager,
)
from lyrveil_orchestrator.state_machine import GpuState, is_legal
from lyrveil_orchestrator.supervisor import ProcessSupervisor, SupervisorTimings

LLM = ContainerName.LLM
IMAGE = ContainerName.IMAGE
TIMINGS = SupervisorTimings(
    llm_load_timeout_s=6.0,
    image_load_timeout_s=6.0,
    release_timeout_s=2.0,
    readiness_poll_s=1.0,
    settle_interval_s=0.5,
)


@pytest.fixture
def anyio_backend() -> str:
    """Runs the async tests on asyncio, the event loop of the orchestrator."""
    return "asyncio"


class StartingGate(FakeContainerGate):
    """A fake gate on which the containers in `starting` report `starting` health for their next `reads` inspects of a
    running container, then the health they hold."""

    def __init__(self) -> None:
        super().__init__()
        self.starting: dict[ContainerName, int] = {}

    async def inspect(self, name: ContainerName) -> ContainerStatus:
        status = await super().inspect(name)
        left = self.starting.get(name, 0)
        if not status.running or left == 0:
            return status
        self.starting[name] = left - 1
        return ContainerStatus(status.name, status.state, True, HealthStatus.STARTING, None)


class World:
    """One gate, one card, the real supervisor and the manager, with the two clocks of the module docstring."""

    def __init__(self) -> None:
        self.gate = StartingGate()
        self.card = FakeGpuProbe()
        self.now = 1000.0
        self.supervisor_now = 0.0
        self.on_sleep: list[Callable[[], None]] = []
        supervisor = ProcessSupervisor(
            self.gate, GPUResourceMonitor(self.card), TIMINGS, sleep=self._sleep, clock=lambda: self.supervisor_now
        )
        self.manager = ModelLifecycleManager(supervisor, clock=lambda: self.now)

    async def _sleep(self, seconds: float) -> None:
        self.supervisor_now += seconds
        if self.on_sleep:
            self.on_sleep.pop(0)()

    def run(self, name: ContainerName, health: HealthStatus | None) -> None:
        """Leaves `name` running with `health`, as a start before this process would have."""
        container = self.gate.containers[name]
        container.state = ContainerState.RUNNING
        container.health = health

    async def settle(self) -> GpuState:
        """Steps until nothing is left to do and returns the state reached."""
        for _ in range(30):
            if not await self.manager.step():
                return self.manager.state
        raise AssertionError("the manager kept changing state")

    def calls(self, *operations: str) -> list[tuple[str, ContainerName]]:
        return [call for call in self.gate.calls if call[0] in operations]

    def moves(self) -> list[tuple[str, str]]:
        return [(event["from"], event["to"]) for event in self.manager.events(None, 0)]

    def running(self) -> set[ContainerName]:
        return {name for name, container in self.gate.containers.items() if container.state is ContainerState.RUNNING}


@pytest.fixture
def world() -> World:
    return World()


@pytest.mark.anyio
async def test_a_healthy_text_runtime_with_the_image_stopped_is_kept(world: World) -> None:
    world.run(LLM, HealthStatus.HEALTHY)
    assert await world.settle() is GpuState.LLM_READY
    assert world.moves() == [("RECOVERING", "LLM_READY")]
    assert world.calls("start", "stop") == []


@pytest.mark.anyio
async def test_a_healthy_image_runtime_with_the_text_runtime_stopped_is_kept(world: World) -> None:
    world.run(IMAGE, HealthStatus.HEALTHY)
    world.manager.request_mode(Mode.IMAGE)
    assert await world.settle() is GpuState.IMAGE_READY
    assert world.moves() == [("RECOVERING", "IMAGE_READY")]
    assert world.calls("start", "stop") == []


@pytest.mark.anyio
@pytest.mark.parametrize("starting_reads", [0, 2], ids=["healthy", "starting"])
async def test_both_running_stops_the_image_runtime_then_keeps_the_text_runtime_once_healthy(
    world: World, caplog: pytest.LogCaptureFixture, starting_reads: int
) -> None:
    world.run(LLM, HealthStatus.HEALTHY)
    world.run(IMAGE, HealthStatus.HEALTHY)
    world.gate.starting[LLM] = starting_reads
    with caplog.at_level(logging.ERROR, logger="lyrveil_orchestrator.lifecycle"):
        assert await world.manager.step()
    assert "EXCLUSIVITY_VIOLATION" in caplog.text
    assert world.manager.state is GpuState.LLM_READY
    # The image runtime's stop was verified: it ended, listed no process and the memory settled.
    assert world.calls("start", "stop", "top") == [("stop", IMAGE), ("top", IMAGE)]
    assert world.running() == {LLM}
    assert world.gate.starting[LLM] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("image", [None, HealthStatus.HEALTHY], ids=["image stopped", "image healthy"])
async def test_an_unhealthy_text_runtime_is_stopped_and_the_gpu_rests_in_idle(
    world: World, image: HealthStatus | None
) -> None:
    world.run(LLM, HealthStatus.UNHEALTHY)
    if image is not None:
        world.run(IMAGE, image)
    world.manager.request_mode(Mode.NONE)
    assert await world.settle() is GpuState.IDLE
    assert world.moves() == [("RECOVERING", "IDLE")]
    assert set(world.calls("stop")) == {("stop", LLM)} | ({("stop", IMAGE)} if image is not None else set())
    assert world.running() == set()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("llm", "expected"),
    [(None, GpuState.IDLE), (HealthStatus.UNHEALTHY, GpuState.IDLE), (HealthStatus.HEALTHY, GpuState.LLM_READY)],
    ids=["text stopped", "text unhealthy", "text healthy"],
)
async def test_an_unhealthy_image_runtime_is_stopped(
    world: World, llm: HealthStatus | None, expected: GpuState
) -> None:
    # A healthy text runtime beside it is the row of both running: the text runtime stays.
    world.run(IMAGE, HealthStatus.UNHEALTHY)
    if llm is not None:
        world.run(LLM, llm)
    world.manager.request_mode(Mode.NONE if expected is GpuState.IDLE else Mode.LLM)
    assert await world.manager.step()
    assert world.manager.state is expected
    assert ("stop", IMAGE) in world.calls("stop")
    assert IMAGE not in world.running()


@pytest.mark.anyio
async def test_a_runtime_found_starting_that_never_turns_healthy_is_stopped(world: World) -> None:
    world.run(LLM, HealthStatus.STARTING)
    assert await world.manager.step()
    # The wait lasted the load timeout on the supervisor's clock, then recovery stopped it and verified the release.
    assert world.manager.state is GpuState.IDLE
    assert world.supervisor_now >= TIMINGS.llm_load_timeout_s
    assert world.calls("stop") == [("stop", LLM)]


@pytest.mark.anyio
async def test_both_stopped_rest_in_idle_once_the_memory_settles(world: World) -> None:
    world.manager.request_mode(Mode.NONE)
    assert await world.settle() is GpuState.IDLE
    assert world.moves() == [("RECOVERING", "IDLE")]
    assert world.calls("start", "stop") == []
    assert world.card.reads >= TIMINGS.settle_samples


@pytest.mark.anyio
@pytest.mark.parametrize("missing", [LLM, IMAGE])
async def test_a_missing_container_ends_in_error_until_the_user_acts(world: World, missing: ContainerName) -> None:
    world.gate.containers[missing].exists = False
    assert await world.settle() is GpuState.ERROR
    assert world.manager.last_error == "RUNTIME_CONTAINER_MISSING"
    assert world.manager.error_retry_at is None
    # No timed retry: an hour later the GPU still waits for `.\setup.cmd` and the user's recover.
    world.now += 3600
    assert await world.settle() is GpuState.ERROR
    world.gate.containers[missing].exists = True
    world.manager.recover()
    assert await world.settle() is GpuState.LLM_READY


@pytest.mark.anyio
async def test_an_unreachable_docker_ends_in_error_and_retries_by_itself(world: World) -> None:
    world.gate.fail(LLM, "inspect")
    assert await world.settle() is GpuState.ERROR
    assert world.manager.last_error == "DOCKER_UNAVAILABLE"
    assert world.manager.error_retry_at == world.now + ERROR_RETRY_S
    world.now += ERROR_RETRY_S - 1
    assert await world.settle() is GpuState.ERROR
    world.now += 1
    assert await world.settle() is GpuState.LLM_READY
    assert ("ERROR", "RECOVERING") in world.moves()
    assert [event["reason"] for event in world.manager.events(None, 0) if event["from"] == "ERROR"] == ["timed retry"]


@pytest.mark.anyio
async def test_timed_retries_happen_every_300_s_and_at_most_three_times_an_hour(world: World) -> None:
    world.gate.fail(LLM, "inspect", times=10)
    assert await world.settle() is GpuState.ERROR
    retries: list[float] = []
    for _ in range(14):
        world.now += ERROR_RETRY_S
        seq = world.manager.seq
        assert await world.settle() is GpuState.ERROR
        if world.manager.seq > seq:
            retries.append(world.now)
    start = retries[0]
    # Three retries 300 s apart, then none until the first of them is an hour old.
    assert [moment - start for moment in retries[:4]] == [0, ERROR_RETRY_S, 2 * ERROR_RETRY_S, 3600]
    assert all(is_legal(GpuState(source), GpuState(target)) for source, target in world.moves())


@pytest.mark.anyio
@pytest.mark.parametrize(("mode", "name"), [(Mode.LLM, LLM), (Mode.IMAGE, IMAGE)])
async def test_a_lease_released_with_oom_restarts_its_healthy_runtime(
    world: World, mode: Mode, name: ContainerName
) -> None:
    text = mode is Mode.LLM
    ready, generating, loading = (
        (GpuState.LLM_READY, "GENERATING_TEXT", "LOADING_LLM")
        if text
        else (GpuState.IMAGE_READY, "GENERATING_IMAGE", "LOADING_IMAGE")
    )
    world.manager.request_mode(mode)
    assert await world.settle() is ready
    lease = world.manager.acquire(mode, "job-1")
    world.manager.release(lease.id, LeaseOutcome.OOM)
    assert world.manager.last_error == ("LLM_OOM" if text else "IMAGE_OOM")
    assert await world.settle() is ready
    # The runtime was still healthy, and recovery stopped it, verified the release and loaded it again.
    assert world.calls("start", "stop") == [("start", name), ("stop", name), ("start", name)]
    assert world.moves()[-4:] == [
        (generating, "RECOVERING"),
        ("RECOVERING", "IDLE"),
        ("IDLE", loading),
        (loading, ready.value),
    ]


# Load failures and launch levels (T-ORC-08, orchestrator side).

_CUDA_OOM = (
    "ggml_backend_cuda_buffer_type_alloc_buffer: allocating 2048.00 MiB on device 0: cudaMalloc failed: out of memory"
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exit_code", "logs", "code", "oom"),
    [
        (3, "", "LLM_LOAD_FAILED", True),
        (1, _CUDA_OOM, "LLM_LOAD_FAILED", True),
        (1, "llama_model_load: error loading model: unknown model architecture", "LLM_LOAD_FAILED", False),
        (78, "", "RUNTIME_CONFIG_INVALID", False),
    ],
    ids=["status 3", "allocation log", "other failure", "launch file refused"],
)
async def test_a_failed_text_load_is_recorded_with_its_classification(
    world: World, exit_code: int, logs: str, code: str, oom: bool
) -> None:
    world.manager.request_mode(Mode.NONE)
    assert await world.settle() is GpuState.IDLE
    world.gate.containers[LLM].health_after_start = HealthStatus.STARTING
    world.on_sleep = [lambda: world.gate.crash(LLM, exit_code=exit_code, logs=logs)]
    world.manager.request_mode(Mode.LLM, "job-1")
    assert await world.manager.step()

    assert (world.manager.state, world.manager.last_error) == (GpuState.RECOVERING, code)
    event = world.manager.events(None, 0)[-1]
    assert (event["from"], event["code"], event["job_id"]) == ("LOADING_LLM", code, "job-1")
    assert event["details"] == {"failures": 1, "oom": int(oom), "exit_code": exit_code}
    document = await world.manager.snapshot()
    assert document["load_failure"] == {
        "seq": event["seq"],
        "mode": "llm",
        "code": code,
        "oom": oom,
        "failures": 1,
        "final": False,
        "job_id": "job-1",
    }
    # The log is read only when the exit status does not decide, and never for a refused launch file.
    assert (("logs", LLM) in world.gate.calls) is (exit_code == 1)


@pytest.mark.anyio
async def test_three_failed_text_loads_end_in_error_and_the_next_load_clears_the_failure(world: World) -> None:
    world.gate.containers[LLM].health_after_start = HealthStatus.UNHEALTHY
    world.gate.containers[LLM].logs = "CUDA error: out of memory"
    assert await world.settle() is GpuState.IDLE
    world.now += 5
    assert await world.settle() is GpuState.IDLE
    world.now += 20
    assert await world.settle() is GpuState.ERROR
    failure = world.manager.load_failure
    assert failure is not None
    assert (failure.code, failure.oom, failure.failures, failure.final) == ("LLM_LOAD_FAILED", True, 3, True)
    world.gate.containers[LLM].health_after_start = HealthStatus.HEALTHY
    world.manager.recover()
    assert await world.settle() is GpuState.LLM_READY
    assert world.manager.load_failure is None
    assert (await world.manager.snapshot())["load_failure"] is None


@pytest.mark.anyio
async def test_three_failed_image_loads_give_the_gpu_back_to_text_and_name_the_job(world: World) -> None:
    assert await world.settle() is GpuState.LLM_READY
    world.gate.containers[IMAGE].health_after_start = HealthStatus.UNHEALTHY
    world.manager.request_mode(Mode.IMAGE, "job-9")
    assert await world.settle() is GpuState.IDLE
    world.now += 5
    assert await world.settle() is GpuState.IDLE
    world.now += 20

    # The third failure ends the image's turn: the text runtime loads again, and the failure stays readable for the
    # backend, which ends the job named.
    assert await world.settle() is GpuState.LLM_READY
    assert world.manager.desired_mode is Mode.LLM
    failure = world.manager.load_failure
    assert failure is not None
    assert (failure.runtime.value, failure.code, failure.oom, failure.failures, failure.final, failure.job_id) == (
        "image",
        "IMAGE_LOAD_FAILED",
        False,
        3,
        True,
        "job-9",
    )
    returned = [event for event in world.manager.events(None, 0) if event["reason"] == "image load failed, mode llm"]
    assert [(event["from"], event["to"]) for event in returned] == [("RECOVERING", "IDLE")]
    assert world.running() == {LLM}

    # The next request for the image gets its full retries, and its load clears the failure.
    world.gate.containers[IMAGE].health_after_start = HealthStatus.HEALTHY
    world.manager.request_mode(Mode.IMAGE, "job-10")
    assert await world.settle() is GpuState.IMAGE_READY
    assert world.manager.load_failure is None
    assert all(is_legal(GpuState(source), GpuState(target)) for source, target in world.moves())
