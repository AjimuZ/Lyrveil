"""T-ORC-04 and the orchestrator side of T-ORC-05 (docs/gpu-orchestration.md, Verifying GPU release).

T-ORC-04: a release is verified although another program takes memory while the runtime stops, and nothing but the two
runtime containers is ever acted on. T-ORC-05: a load the card has too little free memory for is held back in IDLE with
the reason GPU_INSUFFICIENT_VRAM, checked again every 5 s, and made once the memory is free. Ending the job that waits
after `vram.wait_seconds` is the backend's part, tested in backend/tests/integration/test_image_mode_switch.py.

The manager and the supervisor are the real ones over the fake gate and the fake card. The card's used memory is the
external use the test sets plus the footprint of each running runtime. The test moves the manager's clock and calls
`step` directly instead of the run loop. Nothing in the fakes can end a process: the gate acts on the two runtime
containers alone and the probe only reads, so "no external process is touched" is checked on what the gate was asked.
"""

from collections.abc import Callable
from typing import Any

import pytest

from fakes.fake_docker import FakeContainerGate
from fakes.fake_nvml import FakeGpuProbe
from lyrveil_orchestrator.container_gate import ContainerName, ContainerState
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor
from lyrveil_orchestrator.lifecycle import GPU_INSUFFICIENT_VRAM, Mode, ModelLifecycleManager
from lyrveil_orchestrator.state_machine import GpuState
from lyrveil_orchestrator.supervisor import ProcessSupervisor, SupervisorTimings

LLM = ContainerName.LLM
IMAGE = ContainerName.IMAGE
FAST = SupervisorTimings(readiness_poll_s=0.0, settle_interval_s=0.0)
FOOTPRINT = {LLM: 8900, IMAGE: 6200}
TOTAL_MIB = 12282
DESKTOP_MIB = 1489
# Another program that holds this much leaves 5282 MiB free: too little for either runtime.
CROWDED_MIB = 7000


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
    """Waits for nothing: the fake runtimes are ready at once."""


class MemoryGate(FakeContainerGate):
    """A fake gate whose running runtimes hold their footprint on `card`. `on_stop`, when set, runs once after the
    next stop has ended its container: the moment another program may take the memory it gave back."""

    def __init__(self, card: FakeGpuProbe) -> None:
        super().__init__()
        self.card = card
        self.on_stop: Callable[[], None] | None = None

    async def start(self, name: ContainerName) -> bool:
        started = await super().start(name)
        self._hold()
        return started

    async def stop(self, name: ContainerName, grace_s: int) -> bool:
        stopped = await super().stop(name, grace_s)
        self._hold()
        action, self.on_stop = self.on_stop, None
        if action is not None:
            action()
        return stopped

    def _hold(self) -> None:
        running = [name for name, container in self.containers.items() if container.state is ContainerState.RUNNING]
        self.card.runtime_mib = sum(FOOTPRINT[name] for name in running)


class World:
    """One card, one gate and the real supervisor and manager, sharing the test's clock."""

    def __init__(self) -> None:
        self.card = FakeGpuProbe(total_mib=TOTAL_MIB, external_mib=DESKTOP_MIB)
        self.gate = MemoryGate(self.card)
        self.clock = Clock()
        supervisor = ProcessSupervisor(self.gate, GPUResourceMonitor(self.card), FAST, sleep=no_sleep, clock=self.clock)
        self.manager = ModelLifecycleManager(supervisor, clock=self.clock)

    async def settle(self) -> GpuState:
        """Steps until nothing is left to do and returns the state reached."""
        for _ in range(20):
            if not await self.manager.step():
                return self.manager.state
        raise AssertionError("the manager kept changing state")

    def events_after(self, seq: int) -> list[dict[str, Any]]:
        """Returns the events of this process with a `seq` above `seq`."""
        return self.manager.events(self.manager.boot_id, seq)

    def crowd_after_next_stop(self) -> None:
        """Makes another program take CROWDED_MIB as soon as the next runtime stops."""

        def take() -> None:
            self.card.external_mib = CROWDED_MIB

        self.gate.on_stop = take


@pytest.fixture
def world() -> World:
    return World()


@pytest.mark.anyio
async def test_a_release_is_verified_although_another_program_took_memory_and_only_the_runtime_is_touched(
    world: World,
) -> None:
    assert await world.settle() is GpuState.LLM_READY
    world.crowd_after_next_stop()
    world.manager.request_mode(Mode.NONE, "job-1")
    assert await world.settle() is GpuState.IDLE

    release = world.manager.events(None, 0)[-1]
    assert (release["from"], release["to"], release["reason"]) == ("UNLOADING_LLM", "IDLE", "release verified")
    # 10389 MiB before the stop, 7000 after it: the drop is 5511 MiB short of the 8900 MiB the load took. That is
    # informational, since the container ended, and the release is verified all the same.
    details = release["details"]
    assert (details["drop_mib"], details["footprint_mib"], details["drop_shortfall_mib"]) == (3389, 8900, 5511)

    # Only the two runtime containers were ever named, only the text runtime was stopped, and the other program
    # still holds its memory.
    assert {name for _, name in world.gate.calls} <= {LLM, IMAGE}
    assert [call for call in world.gate.calls if call[0] == "stop"] == [("stop", LLM)]
    assert world.card.external_mib == CROWDED_MIB


@pytest.mark.anyio
async def test_low_vram_holds_the_load_in_idle_with_its_reason_and_checks_again_every_5_s(world: World) -> None:
    assert await world.settle() is GpuState.LLM_READY
    world.crowd_after_next_stop()
    world.manager.request_mode(Mode.IMAGE, "job-7")
    assert await world.settle() is GpuState.IDLE

    hold = world.manager.events(None, 0)[-1]
    assert (hold["from"], hold["to"], hold["code"], hold["job_id"]) == ("IDLE", "IDLE", GPU_INSUFFICIENT_VRAM, "job-7")
    assert hold["details"] == {"free_mib": 5282, "required_mib": 6500}
    document = await world.manager.snapshot()
    assert (document["state"], document["blocked_reason"]) == ("IDLE", GPU_INSUFFICIENT_VRAM)
    assert document["blocked_vram"] == {"mode": "image", "free_mib": 5282, "required_mib": 6500}
    assert ("start", IMAGE) not in world.gate.calls

    # Before 5 s nothing is read; at 5 s the card is read again, and a check that still finds too little records
    # nothing new.
    seq, reads = world.manager.seq, world.card.reads
    world.clock.now += 4.0
    assert await world.manager.step() is False
    assert world.card.reads == reads
    world.clock.now += 1.0
    assert await world.manager.step() is False
    assert (world.card.reads, world.manager.seq) == (reads + 1, seq)

    # Once the other program gives its memory back, the next check starts the image runtime and the hold is over.
    world.card.external_mib = DESKTOP_MIB
    world.clock.now += 5.0
    assert await world.settle() is GpuState.IMAGE_READY
    assert [(event["from"], event["to"]) for event in world.events_after(seq)] == [
        ("IDLE", "LOADING_IMAGE"),
        ("LOADING_IMAGE", "IMAGE_READY"),
    ]
    document = await world.manager.snapshot()
    assert (document["blocked_reason"], document["blocked_vram"]) == (None, None)
    assert [call for call in world.gate.calls if call[0] in ("start", "stop")] == [
        ("start", LLM),
        ("stop", LLM),
        ("start", IMAGE),
    ]


@pytest.mark.anyio
async def test_a_hold_follows_the_desired_mode_and_ends_when_no_runtime_is_wanted(world: World) -> None:
    await world.settle()
    world.crowd_after_next_stop()
    world.manager.request_mode(Mode.IMAGE, "job-7")
    await world.settle()

    # The image job gave up and text is wanted again: the text runtime's own requirement applies at once.
    seq = world.manager.seq
    world.manager.request_mode(Mode.LLM)
    assert await world.settle() is GpuState.IDLE
    (text_hold,) = world.events_after(seq)
    assert (text_hold["code"], text_hold["job_id"]) == (GPU_INSUFFICIENT_VRAM, None)
    assert text_hold["details"] == {"free_mib": 5282, "required_mib": 6000}
    assert (await world.manager.snapshot())["blocked_vram"] == {"mode": "llm", "free_mib": 5282, "required_mib": 6000}

    # With mode none nothing waits for memory any more, and the end of the hold is recorded so a long poll sees it.
    seq = world.manager.seq
    world.manager.request_mode(Mode.NONE)
    assert await world.settle() is GpuState.IDLE
    (ended,) = world.events_after(seq)
    assert (ended["from"], ended["to"], ended["code"], ended["reason"]) == ("IDLE", "IDLE", None, "no runtime wanted")
    assert world.manager.blocked is None
    assert (await world.manager.snapshot())["blocked_reason"] is None
