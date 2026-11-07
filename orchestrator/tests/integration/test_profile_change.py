"""T-MOD-03, orchestrator side: the reload of a text profile change through the control API (docs/models.md, Selecting
a profile). The backend's reload sets the desired mode to `none`, waits for IDLE, then sets `llm`; here the orchestrator
must stop the text runtime only once the current lease ends, enter IDLE only after the release is verified, and start
the runtime again for `llm`, which then reads the new launch file. The backend side, including the launch file the
start reads, is backend/tests/integration/test_model_profiles.py.
"""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fakes.fake_docker import FakeContainerGate
from fakes.fake_nvml import FakeGpuProbe
from lyrveil_orchestrator.api import create_app
from lyrveil_orchestrator.container_gate import ContainerName
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor
from lyrveil_orchestrator.lifecycle import ModelLifecycleManager
from lyrveil_orchestrator.supervisor import ProcessSupervisor, SupervisorTimings

TOKEN = "ab" * 32
AUTH = {"Authorization": "Bearer " + TOKEN}
FAST = SupervisorTimings(readiness_poll_s=0.01, settle_interval_s=0.01)


@pytest.fixture
def gate() -> FakeContainerGate:
    return FakeContainerGate()


@pytest.fixture
def client(gate: FakeContainerGate) -> Iterator[TestClient]:
    monitor = GPUResourceMonitor(FakeGpuProbe())
    manager = ModelLifecycleManager(ProcessSupervisor(gate, monitor, FAST), tick_s=0.05)
    with TestClient(create_app(TOKEN, monitor, manager)) as test_client:
        yield test_client


def wait_for_state(client: TestClient, state: str, timeout_s: float = 10.0) -> dict[str, Any]:
    """Long-polls GET /v1/state until it reports `state`, and returns that document."""
    deadline = time.monotonic() + timeout_s
    seq = 0
    while time.monotonic() < deadline:
        document: dict[str, Any] = client.get(f"/v1/state?since={seq}&wait=1", headers=AUTH).json()
        if document["state"] == state:
            return document
        seq = document["seq"]
    raise AssertionError(f"the state never became {state}")


def calls(gate: FakeContainerGate, operation: str) -> list[tuple[str, ContainerName]]:
    return [call for call in gate.calls if call[0] == operation]


def test_mode_none_then_llm_stops_the_text_runtime_after_the_lease_verifies_the_release_and_starts_it_again(
    client: TestClient, gate: FakeContainerGate
) -> None:
    wait_for_state(client, "LLM_READY")
    granted = client.post("/v1/leases", json={"mode": "llm", "job_id": "reply-1"}, headers=AUTH)
    assert granted.status_code == 201
    lease = granted.json()

    # The running reply keeps the runtime: the stop waits for its lease.
    assert client.put("/v1/desired-mode", json={"mode": "none", "job_id": "reload-1"}, headers=AUTH).status_code == 202
    time.sleep(0.3)
    held = client.get("/v1/state", headers=AUTH).json()
    assert (held["state"], held["desired_mode"]) == ("GENERATING_TEXT", "none")
    assert calls(gate, "stop") == []

    released = client.request("DELETE", f"/v1/leases/{lease['id']}", json={"outcome": "ok"}, headers=AUTH)
    assert released.status_code == 200
    idle = wait_for_state(client, "IDLE")
    assert idle["desired_mode"] == "none"
    assert idle["runtimes"]["llm"]["container"] == "exited"
    assert calls(gate, "stop") == [("stop", ContainerName.LLM)]

    assert client.put("/v1/desired-mode", json={"mode": "llm", "job_id": "reload-1"}, headers=AUTH).status_code == 202
    wait_for_state(client, "LLM_READY")
    assert calls(gate, "start") == [("start", ContainerName.LLM), ("start", ContainerName.LLM)]

    events = client.get("/v1/events", headers=AUTH).json()["events"]
    moves = [(event["from"], event["to"]) for event in events]
    unloading = moves.index(("LLM_READY", "UNLOADING_LLM"))
    assert moves[unloading:] == [
        ("LLM_READY", "UNLOADING_LLM"),
        ("UNLOADING_LLM", "IDLE"),
        ("IDLE", "LOADING_LLM"),
        ("LOADING_LLM", "LLM_READY"),
    ]
    verified = events[unloading + 1]
    assert (verified["reason"], verified["details"]["exit_code"]) == ("release verified", 0)
