"""Mode switching for an image job: `llm` to `image` and back along the state machine, with exclusivity.

P05.7 delivered the switch; P06.1 adds T-IMG-04, the 18 steps of an image request (docs/gpu-orchestration.md) played
through the control API one step at a time, and a run that reaches every state of the table.

The real lifecycle manager and supervisor run over the fake gate and the fake probe. `WatchedGate` ties the two fakes
into one card: a started runtime holds its footprint until it stops, and every start is checked against Exclusivity
rules 2 and 3 (docs/gpu-orchestration.md): the other runtime must not run, and the card's memory must be released and
settled. A start that breaks either rule is recorded, so each test can assert that none happened.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes.fake_docker import FakeContainerGate
from fakes.fake_nvml import FakeGpuProbe
from lyrveil_orchestrator.api import create_app
from lyrveil_orchestrator.container_gate import ContainerName, ContainerState, HealthStatus
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor, VramReading
from lyrveil_orchestrator.lifecycle import LeaseRefusedError, Mode, ModelLifecycleManager
from lyrveil_orchestrator.state_machine import GpuState, is_legal
from lyrveil_orchestrator.supervisor import ProcessSupervisor, SupervisorTimings

TOKEN = "ab" * 32
AUTH = {"Authorization": "Bearer " + TOKEN}
LLM = ContainerName.LLM
IMAGE = ContainerName.IMAGE
FAST = SupervisorTimings(readiness_poll_s=0.01, settle_interval_s=0.01, release_timeout_s=0.2)
# What each runtime holds on the card while it runs, in MiB.
FOOTPRINT = {LLM: 8900, IMAGE: 6200}
OTHER = {LLM: IMAGE, IMAGE: LLM}
EXTERNAL_MIB = 1489


@pytest.fixture
def anyio_backend() -> str:
    """Runs the async tests on asyncio, the event loop of the orchestrator."""
    return "asyncio"


@dataclass
class Card(FakeGpuProbe):
    """A FakeGpuProbe whose external use can be made to keep moving, as when a stopped runtime's memory is still being
    given back: while `unsettled` is set, each read swings the external use by 1000 MiB."""

    unsettled: bool = False

    def read_memory(self) -> VramReading:
        if self.unsettled:
            self.external_mib = EXTERNAL_MIB + 1000 if self.external_mib == EXTERNAL_MIB else EXTERNAL_MIB
        elif self.external_mib != EXTERNAL_MIB:
            self.external_mib = EXTERNAL_MIB
        return super().read_memory()


class WatchedGate(FakeContainerGate):
    """A FakeContainerGate whose runtimes hold memory on `card`. `violations` lists each start that came while the
    other runtime ran or while the card's memory had not settled; `starts` lists the runtimes started, in order.

    `runtime_log` lists every start and stop call as (operation, container, stop grace or None, GPU state), where the
    state is the one of the manager passed to `watch` at the moment of the call, None before `watch`.
    """

    def __init__(self, card: Card) -> None:
        super().__init__()
        self.card = card
        self.violations: list[tuple[str, ContainerName]] = []
        self.starts: list[ContainerName] = []
        self.runtime_log: list[tuple[str, ContainerName, int | None, str | None]] = []
        self._manager: ModelLifecycleManager | None = None

    def watch(self, manager: ModelLifecycleManager) -> None:
        """Records `manager`'s state with each later start and stop call."""
        self._manager = manager

    def state_at(self, operation: str, name: ContainerName) -> str | None:
        """Returns the GPU state of the latest `operation` call on `name`. Raises AssertionError when none was made."""
        for logged, container, _, state in reversed(self.runtime_log):
            if (logged, container) == (operation, name):
                return state
        raise AssertionError(f"no {operation} call on {name}")

    async def start(self, name: ContainerName) -> bool:
        self._log("start", name, None)
        if self.containers[OTHER[name]].state is ContainerState.RUNNING:
            self.violations.append(("the other runtime was running", name))
        if self.card.unsettled or self.card.runtime_mib:
            self.violations.append(("the card's memory was not released", name))
        started = await super().start(name)
        if started:
            self.starts.append(name)
            self.card.runtime_mib = FOOTPRINT[name]
        return started

    async def stop(self, name: ContainerName, grace_s: int) -> bool:
        self._log("stop", name, grace_s)
        stopped = await super().stop(name, grace_s)
        if stopped:
            self.card.runtime_mib = 0
        return stopped

    def _log(self, operation: str, name: ContainerName, grace_s: int | None) -> None:
        state = None if self._manager is None else self._manager.state.value
        self.runtime_log.append((operation, name, grace_s, state))


class Clock:
    """The manager's monotonic clock, moved only by a test, so load retries and lease expiry wait for the test. The
    supervisor keeps the real clock, so its release timeout passes in real time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def card() -> Card:
    return Card()


@pytest.fixture
def monitor(card: Card) -> GPUResourceMonitor:
    return GPUResourceMonitor(card)


@pytest.fixture
def gate(card: Card) -> WatchedGate:
    return WatchedGate(card)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def manager(gate: WatchedGate, monitor: GPUResourceMonitor, clock: Clock) -> ModelLifecycleManager:
    return ModelLifecycleManager(ProcessSupervisor(gate, monitor, FAST), clock=clock, tick_s=0.05)


@pytest.fixture
def client(manager: ModelLifecycleManager, monitor: GPUResourceMonitor) -> Iterator[TestClient]:
    with TestClient(create_app(TOKEN, monitor, manager)) as test_client:
        yield test_client


def wait_for_state(client: TestClient, state: str, timeout_s: float = 10.0) -> dict[str, Any]:
    """Long-polls GET /v1/state until it reports `state`, and returns that document."""
    deadline = time.monotonic() + timeout_s
    seq = 0
    while time.monotonic() < deadline:
        response = client.get(f"/v1/state?since={seq}&wait=1", headers=AUTH)
        assert response.status_code == 200
        document: dict[str, Any] = response.json()
        if document["state"] == state:
            return document
        seq = document["seq"]
    raise AssertionError(f"the state never became {state}")


async def settle(manager: ModelLifecycleManager) -> GpuState:
    """Steps until nothing is left to do and returns the state reached."""
    for _ in range(30):
        if not await manager.step():
            return manager.state
    raise AssertionError("the manager kept changing state")


def moves(manager: ModelLifecycleManager) -> list[tuple[str, str]]:
    return [(event["from"], event["to"]) for event in manager.events(None, 0)]


def test_an_image_job_switches_to_image_mode_and_back_along_the_table(
    client: TestClient, gate: WatchedGate, manager: ModelLifecycleManager
) -> None:
    wait_for_state(client, "LLM_READY")

    # Step 6: the backend asks for the image mode on behalf of the job. Until the image runtime is ready, an image
    # lease is refused, and the text runtime takes no new lease either, since the mode is changing.
    accepted = client.put("/v1/desired-mode", json={"mode": "image", "job_id": "job-1"}, headers=AUTH)
    assert accepted.status_code == 202
    ready = wait_for_state(client, "IMAGE_READY")
    assert ready["desired_mode"] == "image"
    assert ready["runtimes"]["llm"] == {"container": "exited", "health": None, "exit_code": 0}
    assert ready["runtimes"]["image"] == {"container": "running", "health": "healthy", "exit_code": None}
    refused = client.post("/v1/leases", json={"mode": "llm", "job_id": "job-2"}, headers=AUTH)
    assert (refused.status_code, refused.json()["code"]) == (409, "NOT_READY")

    lease = client.post("/v1/leases", json={"mode": "image", "job_id": "job-1"}, headers=AUTH).json()
    assert lease["mode"] == "image"
    # Step 14: the desired mode returns to llm, but the image lease keeps the image runtime until it is released
    # (Exclusivity rule 4).
    assert client.put("/v1/desired-mode", json={"mode": "llm"}, headers=AUTH).status_code == 202
    time.sleep(0.3)
    held = client.get("/v1/state", headers=AUTH).json()
    assert (held["state"], held["runtimes"]["image"]["container"]) == ("GENERATING_IMAGE", "running")
    released = client.request("DELETE", f"/v1/leases/{lease['id']}", json={"outcome": "ok"}, headers=AUTH)
    assert released.status_code == 200

    back = wait_for_state(client, "LLM_READY")
    assert back["runtimes"]["image"] == {"container": "exited", "health": None, "exit_code": 0}
    assert manager.last_error is None

    events = client.get("/v1/events", headers=AUTH).json()["events"]
    path = [(event["from"], event["to"]) for event in events]
    assert path == [
        ("RECOVERING", "IDLE"),
        ("IDLE", "LOADING_LLM"),
        ("LOADING_LLM", "LLM_READY"),
        ("LLM_READY", "UNLOADING_LLM"),
        ("UNLOADING_LLM", "IDLE"),
        ("IDLE", "LOADING_IMAGE"),
        ("LOADING_IMAGE", "IMAGE_READY"),
        ("IMAGE_READY", "GENERATING_IMAGE"),
        ("GENERATING_IMAGE", "IMAGE_READY"),
        ("IMAGE_READY", "UNLOADING_IMAGE"),
        ("UNLOADING_IMAGE", "IDLE"),
        ("IDLE", "LOADING_LLM"),
        ("LOADING_LLM", "LLM_READY"),
    ]
    assert all(is_legal(GpuState(source), GpuState(target)) for source, target in path)
    assert [event["job_id"] for event in events if event["to"] == "GENERATING_IMAGE"] == ["job-1"]
    # Each release was verified with the runtime's memory back: the drop is the footprint the load measured.
    releases = [event["details"] for event in events if event["to"] == "IDLE" and event["from"] != "RECOVERING"]
    assert [(details["drop_mib"], details["footprint_mib"]) for details in releases] == [
        (FOOTPRINT[LLM], FOOTPRINT[LLM]),
        (FOOTPRINT[IMAGE], FOOTPRINT[IMAGE]),
    ]
    assert gate.starts == [LLM, IMAGE, LLM]
    assert gate.violations == []


@pytest.mark.anyio
async def test_a_runtime_never_starts_while_the_other_runs(
    manager: ModelLifecycleManager, gate: WatchedGate, clock: Clock
) -> None:
    assert await settle(manager) is GpuState.LLM_READY
    manager.request_mode(Mode.IMAGE)
    # The text runtime is stopped and released, then something outside Lyrveil starts it again before the image load.
    await manager.step()
    assert manager.state is GpuState.IDLE
    gate.containers[LLM].state = ContainerState.RUNNING
    gate.containers[LLM].health = HealthStatus.HEALTHY

    # Exclusivity rule 3: the image start is refused before Docker is asked, and recovery takes over.
    await manager.step()
    assert (manager.state, manager.last_error) == (GpuState.RECOVERING, "EXCLUSIVITY_VIOLATION")
    assert gate.starts == [LLM]
    with pytest.raises(LeaseRefusedError, match="NOT_READY"):
        manager.acquire(Mode.IMAGE, "job-1")

    # Recovery keeps the healthy text runtime, then the desired mode moves to image through a verified release; the
    # refused start counts as a failed load, so the image load waits for the first retry delay.
    assert await settle(manager) is GpuState.IDLE
    assert gate.containers[LLM].state is ContainerState.EXITED
    clock.now += 5
    assert await settle(manager) is GpuState.IMAGE_READY
    assert gate.starts == [LLM, IMAGE]
    assert gate.containers[LLM].state is ContainerState.EXITED
    assert gate.violations == []
    assert all(is_legal(GpuState(source), GpuState(target)) for source, target in moves(manager))


@pytest.mark.anyio
async def test_a_runtime_never_starts_while_the_other_ones_memory_is_not_released(
    manager: ModelLifecycleManager, gate: WatchedGate, card: Card
) -> None:
    assert await settle(manager) is GpuState.LLM_READY
    manager.request_mode(Mode.IMAGE)
    card.unsettled = True

    # The text runtime ends, but the card's memory keeps moving past the release timeout: the stop is not verified,
    # and recovery, with both runtimes stopped, still waits for the memory before IDLE. It does not settle, so the
    # state ends in ERROR and the image runtime is never started.
    assert await settle(manager) is GpuState.ERROR
    assert manager.last_error == "GPU_RELEASE_UNVERIFIED"
    assert gate.containers[LLM].state is ContainerState.EXITED
    assert gate.starts == [LLM]
    assert ("UNLOADING_LLM", "RECOVERING") in moves(manager)
    assert ("RECOVERING", "IDLE") not in moves(manager)[1:]

    # Once the memory settles, the user's recover verifies the release, and only then does the image runtime start.
    card.unsettled = False
    manager.recover()
    assert await settle(manager) is GpuState.IMAGE_READY
    assert moves(manager)[-4:] == [
        ("ERROR", "RECOVERING"),
        ("RECOVERING", "IDLE"),
        ("IDLE", "LOADING_IMAGE"),
        ("LOADING_IMAGE", "IMAGE_READY"),
    ]
    assert gate.starts == [LLM, IMAGE]
    assert gate.violations == []
    assert all(is_legal(GpuState(source), GpuState(target)) for source, target in moves(manager))


# T-IMG-04. The image job, and the reply queued while it renders, as the backend's scheduler names them.
JOB = "job-image"
REPLY = "job-reply"
# The State column of docs/gpu-orchestration.md, The 18 steps of an image request, written out again so that a change
# to the flow must change this too. A step that spans two states there ("UNLOADING_LLM to IDLE") is split: the stop or
# start call happens in the first state, and the next step begins in the second.
EIGHTEEN_STEPS = [
    (1, "LLM_READY"),
    (2, "LLM_READY"),
    (3, "GENERATING_TEXT"),
    (4, "LLM_READY"),
    (5, "LLM_READY"),
    (6, "UNLOADING_LLM"),
    (7, "IDLE"),
    (8, "LOADING_IMAGE"),
    (9, "IMAGE_READY"),
    (10, "GENERATING_IMAGE"),
    (11, "GENERATING_IMAGE"),
    (12, "IMAGE_READY"),
    (13, "IMAGE_READY"),
    (14, "UNLOADING_IMAGE"),
    (15, "IDLE"),
    (16, "LOADING_LLM"),
    (17, "LLM_READY"),
    (18, "GENERATING_TEXT"),
]


async def eighteen_steps(
    api: httpx.AsyncClient, manager: ModelLifecycleManager, gate: WatchedGate
) -> tuple[list[tuple[int, str | None]], dict[str, Any]]:
    """Plays the 18 steps of an image request as the backend drives them through the control API, with `step` called
    in place of the run loop. Returns (step, state) for each step, the state after it or, for a step that is a
    container call, the state the call happened in; and the reply's text lease, which is still held at the end.
    """
    trace: list[tuple[int, str | None]] = []

    async def state() -> str:
        response = await api.get("/v1/state")
        assert response.status_code == 200
        return str(response.json()["state"])

    async def lease(mode: str, job_id: str) -> dict[str, Any]:
        response = await api.post("/v1/leases", json={"mode": mode, "job_id": job_id})
        assert response.status_code == 201
        granted: dict[str, Any] = response.json()
        return granted

    async def release(granted: dict[str, Any]) -> None:
        response = await api.request("DELETE", f"/v1/leases/{granted['id']}", json={"outcome": "ok"})
        assert response.status_code == 200

    async def ask(mode: str, job_id: str) -> None:
        response = await api.put("/v1/desired-mode", json={"mode": mode, "job_id": job_id})
        assert response.status_code == 202

    gate.watch(manager)
    assert await settle(manager) is GpuState.LLM_READY
    # Steps 1 and 2 are the backend's alone: it receives the request and stores the job. The GPU stays as it is.
    trace += [(1, await state()), (2, await state())]
    # Steps 3 and 4: the prompt stage runs under a text lease, and its result is committed before the release.
    prompt = await lease("llm", JOB)
    trace.append((3, await state()))
    await release(prompt)
    trace.append((4, await state()))
    # Step 5: the scheduler starts nothing else, so no lease is held and the GPU has nothing to do.
    assert not await manager.step()
    trace.append((5, await state()))
    # Steps 6 and 7: the scheduler asks for the image mode on the job's behalf. One step stops KoboldCpp and verifies
    # the release before IDLE.
    await ask("image", JOB)
    assert await manager.step()
    trace += [(6, gate.state_at("stop", LLM)), (7, await state())]
    # Steps 8 and 9: ComfyUI starts, and its health check passes before IMAGE_READY.
    assert await manager.step()
    trace += [(8, gate.state_at("start", IMAGE)), (9, await state())]
    # Steps 10 and 11: under the image lease ComfyUI loads the checkpoint as the workflow's first node and runs the
    # workflow; the heartbeat keeps the lease meanwhile.
    render = await lease("image", JOB)
    trace.append((10, await state()))
    assert (await api.post(f"/v1/leases/{render['id']}/heartbeat")).status_code == 200
    trace.append((11, await state()))
    # Steps 12 and 13: the image is fetched to staging and the lease released; `persist` and the message update need
    # no GPU, so they may finish after step 14.
    await release(render)
    trace += [(12, await state()), (13, await state())]
    # Steps 14 and 15: the reply queued during the render brings the desired mode back to llm, on its behalf. One step
    # stops ComfyUI and verifies the release.
    await ask("llm", REPLY)
    assert await manager.step()
    trace += [(14, gate.state_at("stop", IMAGE)), (15, await state())]
    # Steps 16 and 17: KoboldCpp starts, and its health means that the text model is loaded.
    assert await manager.step()
    trace += [(16, gate.state_at("start", LLM)), (17, await state())]
    # Step 18: the scheduler runs the queued reply under a text lease.
    reply = await lease("llm", REPLY)
    trace.append((18, await state()))
    return trace, reply


@pytest.mark.anyio
async def test_t_img_04_an_image_request_alternates_the_gpu_in_the_documented_order(
    manager: ModelLifecycleManager, gate: WatchedGate, monitor: GPUResourceMonitor
) -> None:
    transport = httpx.ASGITransport(app=create_app(TOKEN, monitor, manager))
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator", headers=AUTH) as api:
        trace, _ = await eighteen_steps(api, manager, gate)
        events = (await api.get("/v1/events")).json()["events"]

    assert trace == EIGHTEEN_STEPS
    # After the text runtime's start at boot, one stop and one start each way, with the documented grace periods
    # (`stop?t=20` and `stop?t=30`), each in the state of its step, and the other runtime never running at a start.
    assert gate.runtime_log == [
        ("start", LLM, None, "LOADING_LLM"),
        ("stop", LLM, 20, "UNLOADING_LLM"),
        ("start", IMAGE, None, "LOADING_IMAGE"),
        ("stop", IMAGE, 30, "UNLOADING_IMAGE"),
        ("start", LLM, None, "LOADING_LLM"),
    ]
    assert gate.violations == []
    # Every transition is in the table, and the switch to image and back names the job that asked for it.
    assert [(event["from"], event["to"], event["job_id"]) for event in events] == [
        ("RECOVERING", "IDLE", None),
        ("IDLE", "LOADING_LLM", None),
        ("LOADING_LLM", "LLM_READY", None),
        ("LLM_READY", "GENERATING_TEXT", JOB),
        ("GENERATING_TEXT", "LLM_READY", JOB),
        ("LLM_READY", "UNLOADING_LLM", JOB),
        ("UNLOADING_LLM", "IDLE", JOB),
        ("IDLE", "LOADING_IMAGE", JOB),
        ("LOADING_IMAGE", "IMAGE_READY", JOB),
        ("IMAGE_READY", "GENERATING_IMAGE", JOB),
        ("GENERATING_IMAGE", "IMAGE_READY", JOB),
        ("IMAGE_READY", "UNLOADING_IMAGE", REPLY),
        ("UNLOADING_IMAGE", "IDLE", REPLY),
        ("IDLE", "LOADING_LLM", REPLY),
        ("LOADING_LLM", "LLM_READY", REPLY),
        ("LLM_READY", "GENERATING_TEXT", REPLY),
    ]
    assert all(is_legal(GpuState(event["from"]), GpuState(event["to"])) for event in events)
    # Step 7 and step 15 verified the release: the memory dropped by the footprint each load measured.
    releases = [event["details"] for event in events if event["reason"] == "release verified"]
    assert [(details["drop_mib"], details["footprint_mib"]) for details in releases] == [
        (FOOTPRINT[LLM], FOOTPRINT[LLM]),
        (FOOTPRINT[IMAGE], FOOTPRINT[IMAGE]),
    ]


@pytest.mark.anyio
async def test_the_image_flow_and_a_lost_docker_reach_every_state_of_the_table(
    manager: ModelLifecycleManager, gate: WatchedGate, monitor: GPUResourceMonitor
) -> None:
    transport = httpx.ASGITransport(app=create_app(TOKEN, monitor, manager))
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator", headers=AUTH) as api:
        _, reply = await eighteen_steps(api, manager, gate)
        # The reply's runtime fails while Docker stops answering: recovery cannot read the containers and ends in
        # ERROR, which the user's recover leaves for the healthy text runtime.
        failed = await api.request("DELETE", f"/v1/leases/{reply['id']}", json={"outcome": "runtime_error"})
        assert (failed.status_code, failed.json()["state"]) == (200, "RECOVERING")
        gate.fail(LLM, "inspect")
        assert await manager.step()
        assert (manager.state, manager.last_error) == (GpuState.ERROR, "DOCKER_UNAVAILABLE")
        assert (await api.post("/v1/recover")).status_code == 202
        assert await settle(manager) is GpuState.LLM_READY
        events = (await api.get("/v1/events")).json()["events"]

    reached = {event["from"] for event in events} | {event["to"] for event in events}
    assert reached == {state.value for state in GpuState}
    assert gate.violations == []
