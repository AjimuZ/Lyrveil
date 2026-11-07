"""Mode switching for an image job (P05.7): `llm` to `image` and back along the state machine, with exclusivity.

The real lifecycle manager and supervisor run over the fake gate and the fake probe. `WatchedGate` ties the two fakes
into one card: a started runtime holds its footprint until it stops, and every start is checked against Exclusivity
rules 2 and 3 (docs/gpu-orchestration.md): the other runtime must not run, and the card's memory must be released and
settled. A start that breaks either rule is recorded, so each test can assert that none happened.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

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
    other runtime ran or while the card's memory had not settled; `starts` lists the runtimes started, in order."""

    def __init__(self, card: Card) -> None:
        super().__init__()
        self.card = card
        self.violations: list[tuple[str, ContainerName]] = []
        self.starts: list[ContainerName] = []

    async def start(self, name: ContainerName) -> bool:
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
        stopped = await super().stop(name, grace_s)
        if stopped:
            self.card.runtime_mib = 0
        return stopped


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
