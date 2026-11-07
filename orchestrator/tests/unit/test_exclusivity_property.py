"""T-ORC-02: no sequence of events, however it mixes requests, leases, failures and time, runs both runtimes.

Hypothesis draws random event sequences and drives them through the real ModelLifecycleManager and ProcessSupervisor
over the fake gate and the fake probe (docs/gpu-orchestration.md, Exclusivity rules). After every event the manager
steps until it rests, as its run loop does, and an event wrapped in `During` arrives instead while the next container
call is in flight, as a control API request or a fault can arrive during a step. At every container call and after
every step the test checks that the two containers never run together, that Lyrveil never starts one while the other
runs, and that a lease exists only in the generating state of its mode (rule 4); after every step, also that a state's
runtime is the only one running. Every transition goes through state_machine.transition, so an illegal one raises and
fails the example.

Besides the backend's calls, the events include what happens outside Lyrveil: a runtime crashes or turns unhealthy,
the next start fails, Docker or NVML stop answering, the card's memory keeps moving, another program takes enough of it
to hold a load back in IDLE (P06.2) or gives it back, time passes, and a runtime is started by hand while the GPU rests
in a state that holds no runtime and nothing runs, as `docker start` would. A start
by hand during a container call is left out: between the supervisor's inspect of the other container and its own start
(rule 3), such a start would break the rule from outside Lyrveil, and recovery is what handles it.
"""

import asyncio
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fakes.fake_docker import FakeContainerGate
from fakes.fake_nvml import FakeGpuProbe
from lyrveil_orchestrator.container_gate import (
    ContainerName,
    ContainerState,
    ContainerStatus,
    HealthStatus,
    ProcessTable,
)
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor, VramReading
from lyrveil_orchestrator.lifecycle import (
    Lease,
    LeaseExpiredError,
    LeaseOutcome,
    LeaseRefusedError,
    LifecycleConflictError,
    Mode,
    ModelLifecycleManager,
)
from lyrveil_orchestrator.state_machine import STATE_RUNTIME, GpuState, Runtime
from lyrveil_orchestrator.supervisor import CONTAINERS, ProcessSupervisor, SupervisorTimings

LLM = ContainerName.LLM
IMAGE = ContainerName.IMAGE
OTHER = {LLM: IMAGE, IMAGE: LLM}
OTHER_RUNTIME = {Runtime.LLM: Runtime.IMAGE, Runtime.IMAGE: Runtime.LLM}
GENERATING = {Mode.LLM: GpuState.GENERATING_TEXT, Mode.IMAGE: GpuState.GENERATING_IMAGE}
FOOTPRINT = {LLM: 8900, IMAGE: 6200}
EXTERNAL_MIB = 1489
# More steps than any path to a resting state takes: three failed loads with their recoveries, then ERROR.
MAX_STEPS = 30
# Short limits, on a supervisor clock that only the fake sleep moves, so a load that never becomes healthy and memory
# that never settles end within a few iterations.
TIMINGS = SupervisorTimings(
    llm_load_timeout_s=6.0,
    image_load_timeout_s=6.0,
    release_timeout_s=2.0,
    readiness_poll_s=1.0,
    settle_interval_s=0.5,
)


@dataclass
class Card(FakeGpuProbe):
    """A FakeGpuProbe whose external use is the desktop's plus `taken_mib`, held by another program, and swings by
    1000 MiB on every read while `unsettled` is set."""

    unsettled: bool = False
    taken_mib: int = 0

    def read_memory(self) -> VramReading:
        base = EXTERNAL_MIB + self.taken_mib
        if self.unsettled:
            self.external_mib = base + 1000 if self.external_mib == base else base
        else:
            self.external_mib = base
        return super().read_memory()


class ExclusiveGate(FakeContainerGate):
    """A FakeContainerGate whose running runtimes hold their footprint on `card`.

    `violations` lists every start that Lyrveil made while the other runtime was running. `on_call`, when set, runs at
    the beginning of every call, before the fake answers: the moment a call is in flight.
    """

    def __init__(self, card: Card) -> None:
        super().__init__()
        self.card = card
        self.violations: list[ContainerName] = []
        self.on_call: Callable[[], None] | None = None

    def running(self) -> set[ContainerName]:
        return {name for name, container in self.containers.items() if container.state is ContainerState.RUNNING}

    def hold_memory(self) -> None:
        """Sets the card's runtime memory to the footprints of the running containers."""
        self.card.runtime_mib = sum(FOOTPRINT[name] for name in self.running())

    async def inspect(self, name: ContainerName) -> ContainerStatus:
        self._in_flight()
        return await super().inspect(name)

    async def start(self, name: ContainerName) -> bool:
        self._in_flight()
        if OTHER[name] in self.running():
            self.violations.append(name)
        started = await super().start(name)
        self.hold_memory()
        return started

    async def stop(self, name: ContainerName, grace_s: int) -> bool:
        self._in_flight()
        stopped = await super().stop(name, grace_s)
        self.hold_memory()
        return stopped

    async def top(self, name: ContainerName) -> ProcessTable:
        self._in_flight()
        return await super().top(name)

    def _in_flight(self) -> None:
        if self.on_call is not None:
            self.on_call()


class SupervisorClock:
    """The supervisor's monotonic clock, moved only by its sleep, so its waits pass at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class Clock:
    """The manager's monotonic clock, moved only by an Advance event."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# The events. The first group is what the backend sends through the control API, the second what happens outside
# Lyrveil. Each one is applied without awaiting, as the control API's handlers and the world are.


@dataclass(frozen=True)
class RequestMode:
    mode: Mode


@dataclass(frozen=True)
class Acquire:
    mode: Mode


@dataclass(frozen=True)
class Heartbeat:
    pass


@dataclass(frozen=True)
class Release:
    outcome: LeaseOutcome


@dataclass(frozen=True)
class Drain:
    pass


@dataclass(frozen=True)
class Resume:
    pass


@dataclass(frozen=True)
class Recover:
    pass


@dataclass(frozen=True)
class Advance:
    seconds: float


@dataclass(frozen=True)
class Crash:
    name: ContainerName


@dataclass(frozen=True)
class TurnUnhealthy:
    name: ContainerName


@dataclass(frozen=True)
class NextStartHealth:
    name: ContainerName
    health: HealthStatus | None


@dataclass(frozen=True)
class DockerFails:
    name: ContainerName
    operation: str


@dataclass(frozen=True)
class NvmlFails:
    times: int


@dataclass(frozen=True)
class MemoryMoving:
    moving: bool


@dataclass(frozen=True)
class OtherProgram:
    """Another program holds `mib` of the card: enough to hold a load back in IDLE, or 0 when it gives it back."""

    mib: int


Concurrent = (
    RequestMode
    | Acquire
    | Heartbeat
    | Release
    | Drain
    | Resume
    | Recover
    | Advance
    | Crash
    | TurnUnhealthy
    | NextStartHealth
    | DockerFails
    | NvmlFails
    | MemoryMoving
    | OtherProgram
)


@dataclass(frozen=True)
class StartedByHand:
    """A runtime started outside Lyrveil, applied only while the GPU rests in a state without a runtime."""

    name: ContainerName


@dataclass(frozen=True)
class During:
    """`event` arrives while the next container call is in flight, or after the step when no call comes."""

    event: Concurrent


Event = Concurrent | StartedByHand | During

_names = st.sampled_from([LLM, IMAGE])
# Healthy, failed, never healthy within the load timeout, or without a health check.
_start_health = st.sampled_from([HealthStatus.HEALTHY, HealthStatus.UNHEALTHY, HealthStatus.STARTING, None])
CONCURRENT: st.SearchStrategy[Concurrent] = st.one_of(
    st.builds(RequestMode, st.sampled_from(list(Mode))),
    st.builds(Acquire, st.sampled_from([Mode.LLM, Mode.IMAGE])),
    st.just(Heartbeat()),
    st.builds(Release, st.sampled_from(list(LeaseOutcome))),
    st.just(Drain()),
    st.just(Resume()),
    st.just(Recover()),
    st.builds(Advance, st.sampled_from([1.0, 5.0, 20.0, 31.0])),
    st.builds(Crash, _names),
    st.builds(TurnUnhealthy, _names),
    st.builds(NextStartHealth, _names, _start_health),
    st.builds(DockerFails, _names, st.sampled_from(["inspect", "start", "stop", "top"])),
    st.builds(NvmlFails, st.integers(min_value=1, max_value=4)),
    st.builds(MemoryMoving, st.booleans()),
    st.builds(OtherProgram, st.sampled_from([0, 6000])),
)
EVENTS: st.SearchStrategy[Event] = st.one_of(
    CONCURRENT,
    st.builds(StartedByHand, _names),
    st.builds(During, CONCURRENT),
)


class World:
    """One card, one gate, one supervisor and one manager, with the lease the backend holds, if any, and the events
    waiting for the next container call."""

    def __init__(self) -> None:
        self.card = Card()
        self.gate = ExclusiveGate(self.card)
        self.clock = Clock()
        supervisor_clock = SupervisorClock()
        supervisor = ProcessSupervisor(
            self.gate, GPUResourceMonitor(self.card), TIMINGS, sleep=supervisor_clock.sleep, clock=supervisor_clock
        )
        self.manager = ModelLifecycleManager(supervisor, clock=self.clock)
        self.lease: Lease | None = None
        self.in_flight: deque[Concurrent] = deque()
        self.gate.on_call = self._during_call

    def check(self, in_flight: bool = False) -> None:
        """Asserts the exclusivity properties of the module docstring for the current moment.

        While a call is `in_flight`, a state's runtime may still meet the other one running: a LOADING state is
        entered before the supervisor inspects the other container, which a hand may have started, and refuses the
        start. Between steps that state is already left, so the rule is checked there.
        """
        running = self.gate.running()
        assert running != {LLM, IMAGE}, "both runtimes are running"
        assert self.gate.violations == [], "a runtime was started while the other one ran"
        runtime = STATE_RUNTIME[self.manager.state]
        if runtime is not None and not in_flight:
            assert CONTAINERS[OTHER_RUNTIME[runtime]] not in running, f"{self.manager.state} with the other running"
        held = self.manager.lease
        if held is not None:
            assert self.manager.state is GENERATING[held.mode], f"a {held.mode} lease in {self.manager.state}"

    async def apply(self, event: Event) -> None:
        """Applies `event`, then lets the manager step until it rests. Events that never met a container call arrive
        once the steps end, and the manager rests again."""
        match event:
            case During(concurrent):
                self.in_flight.append(concurrent)
            case StartedByHand(name):
                self._start_by_hand(name)
            case _:
                self._apply_now(event)
        self.check()
        await self._rest()
        while self.in_flight:
            self._apply_now(self.in_flight.popleft())
            self.check()
            await self._rest()

    async def _rest(self) -> None:
        for _ in range(MAX_STEPS):
            progressed = await self.manager.step()
            self.check()
            if not progressed:
                return

    def _during_call(self) -> None:
        self.check(in_flight=True)
        if self.in_flight:
            self._apply_now(self.in_flight.popleft())
            self.check(in_flight=True)

    def _start_by_hand(self, name: ContainerName) -> None:
        # Only where the GPU state holds no runtime and nothing runs: anywhere else the hand that starts a second
        # runtime, not Lyrveil, would break the rule.
        if STATE_RUNTIME[self.manager.state] is not None or self.gate.running():
            return
        container = self.gate.containers[name]
        container.state = ContainerState.RUNNING
        container.health = container.health_after_start
        container.exit_code = 0
        self.gate.hold_memory()

    def _apply_now(self, event: Concurrent) -> None:
        """Applies `event` as the backend or the world outside Lyrveil would. A refusal is a valid answer."""
        manager, gate = self.manager, self.gate
        match event:
            case RequestMode(mode):
                with suppress(LifecycleConflictError):
                    manager.request_mode(mode, "job-mode")
            case Acquire(mode):
                with suppress(LeaseRefusedError):
                    self.lease = manager.acquire(mode, "job-lease")
            case Heartbeat():
                if self.lease is not None:
                    with suppress(LeaseExpiredError):
                        self.lease = manager.heartbeat(self.lease.id)
            case Release(outcome):
                if self.lease is not None:
                    with suppress(LeaseExpiredError):
                        manager.release(self.lease.id, outcome)
                    self.lease = None
            case Drain():
                manager.drain()
            case Resume():
                manager.resume()
            case Recover():
                with suppress(LifecycleConflictError):
                    manager.recover()
            case Advance(seconds):
                self.clock.now += seconds
            case Crash(name):
                if name in gate.running():
                    gate.crash(name)
                    gate.hold_memory()
            case TurnUnhealthy(name):
                if name in gate.running():
                    gate.containers[name].health = HealthStatus.UNHEALTHY
            case NextStartHealth(name, health):
                gate.containers[name].health_after_start = health
            case DockerFails(name, operation):
                gate.fail(name, operation)
            case NvmlFails(times):
                self.card.fail_next(times=times)
            case MemoryMoving(moving):
                self.card.unsettled = moving
            case OtherProgram(mib):
                self.card.taken_mib = mib


async def play(events: list[Event]) -> World:
    """Runs `events` on a new world, from process start, and returns it. Fails at the first broken property."""
    world = World()
    world.check()
    await world.apply(Advance(0.0))
    for event in events:
        await world.apply(event)
    return world


@settings(database=None, max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(EVENTS, max_size=30))
def test_no_event_sequence_runs_both_runtimes(events: list[Event]) -> None:
    asyncio.run(play(events))


def test_a_switch_and_back_with_a_lease_in_each_mode_and_a_drain() -> None:
    # A fixed sequence for the record, with the states the run loop passes through.
    world = asyncio.run(
        play(
            [
                Acquire(Mode.LLM),
                Release(LeaseOutcome.OK),
                RequestMode(Mode.IMAGE),
                Acquire(Mode.IMAGE),
                Release(LeaseOutcome.OK),
                RequestMode(Mode.LLM),
                Drain(),
                Resume(),
            ]
        )
    )
    assert world.manager.state is GpuState.LLM_READY
    assert [event["to"] for event in world.manager.events(None, 0)] == [
        "IDLE",
        "LOADING_LLM",
        "LLM_READY",
        "GENERATING_TEXT",
        "LLM_READY",
        "UNLOADING_LLM",
        "IDLE",
        "LOADING_IMAGE",
        "IMAGE_READY",
        "GENERATING_IMAGE",
        "IMAGE_READY",
        "UNLOADING_IMAGE",
        "IDLE",
        "LOADING_LLM",
        "LLM_READY",
        "UNLOADING_LLM",
        "IDLE",
        "LOADING_LLM",
        "LLM_READY",
    ]


def test_a_runtime_started_by_hand_is_never_joined_by_the_other() -> None:
    # Rule 3 alone protects this path: the image runtime is started outside Lyrveil while the GPU rests in IDLE, and
    # the text runtime that the mode then asks for must not start next to it. Recovery adopts the image runtime and
    # stops it for the desired mode; the refused start counts as a failed load, so the text load waits 5 s. The image
    # runtime leaves too little free memory for the text runtime, but the VRAM check (P06.2) sees a runtime container
    # running and does not hold the load back, so this memory is never mistaken for another program's.
    world = asyncio.run(play([RequestMode(Mode.NONE), StartedByHand(IMAGE), RequestMode(Mode.LLM), Advance(5.0)]))
    events = world.manager.events(None, 0)
    # After the boot's three transitions and the stop for mode none.
    switch = [(event["from"], event["to"], event["code"]) for event in events[5:]]
    assert switch == [
        ("IDLE", "LOADING_LLM", None),
        ("LOADING_LLM", "RECOVERING", "EXCLUSIVITY_VIOLATION"),
        ("RECOVERING", "IMAGE_READY", None),
        ("IMAGE_READY", "UNLOADING_IMAGE", None),
        ("UNLOADING_IMAGE", "IDLE", None),
        ("IDLE", "LOADING_LLM", None),
        ("LOADING_LLM", "LLM_READY", None),
    ]
    assert world.gate.running() == {LLM}
    assert world.gate.violations == []
