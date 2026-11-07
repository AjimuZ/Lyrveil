"""The text runtime through the control API: start from the default mode `llm`, a lease, and the stop on drain.

The application runs in process with its lifespan, so the lifecycle manager's run loop drives the fake gate and the
fake probe as it would drive Docker and NVML.
"""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fakes.fake_docker import FakeContainerGate
from fakes.fake_nvml import FakeGpuProbe
from lyrveil_orchestrator.api import create_app
from lyrveil_orchestrator.container_gate import ContainerName, ContainerState
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor
from lyrveil_orchestrator.lifecycle import ModelLifecycleManager
from lyrveil_orchestrator.state_machine import GpuState, is_legal
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
        response = client.get(f"/v1/state?since={seq}&wait=1", headers=AUTH)
        assert response.status_code == 200
        document: dict[str, Any] = response.json()
        if document["state"] == state:
            return document
        seq = document["seq"]
    raise AssertionError(f"the state never became {state}")


def calls(gate: FakeContainerGate, operation: str) -> list[tuple[str, ContainerName]]:
    return [call for call in gate.calls if call[0] == operation]


def test_the_text_runtime_starts_serves_a_lease_and_stops_on_drain(client: TestClient, gate: FakeContainerGate) -> None:
    ready = wait_for_state(client, "LLM_READY")
    assert (ready["desired_mode"], ready["lease"], ready["last_error"]) == ("llm", None, None)
    assert ready["runtimes"]["llm"] == {"container": "running", "health": "healthy", "exit_code": None}
    assert ready["runtimes"]["image"]["container"] == "created"
    assert ready["vram"]["total_mib"] == 12282

    granted = client.post("/v1/leases", json={"mode": "llm", "job_id": "job-1", "ttl_s": 30}, headers=AUTH)
    assert granted.status_code == 201
    lease = granted.json()
    assert (lease["mode"], lease["job_id"], lease["ttl_s"]) == ("llm", "job-1", 30)
    held = client.post("/v1/leases", json={"mode": "llm", "job_id": "job-2"}, headers=AUTH)
    assert (held.status_code, held.json()["code"]) == (409, "LEASE_HELD")
    assert client.post(f"/v1/leases/{lease['id']}/heartbeat", headers=AUTH).status_code == 200
    assert client.get("/v1/state", headers=AUTH).json()["state"] == "GENERATING_TEXT"
    released = client.request("DELETE", f"/v1/leases/{lease['id']}", json={"outcome": "ok"}, headers=AUTH)
    assert released.json() == {"released": True, "state": "LLM_READY"}
    again = client.request("DELETE", f"/v1/leases/{lease['id']}", json={"outcome": "ok"}, headers=AUTH)
    assert (again.status_code, again.json()["code"]) == (410, "LEASE_EXPIRED")
    expired = client.post(f"/v1/leases/{lease['id']}/heartbeat", headers=AUTH)
    assert (expired.status_code, expired.json()["code"]) == (410, "LEASE_EXPIRED")

    assert client.post("/v1/drain", headers=AUTH).status_code == 202
    idle = wait_for_state(client, "IDLE")
    assert idle["draining"] is True
    assert idle["runtimes"]["llm"] == {"container": "exited", "health": None, "exit_code": 0}
    refused = client.post("/v1/leases", json={"mode": "llm", "job_id": "job-3"}, headers=AUTH)
    assert (refused.status_code, refused.json()["code"]) == (409, "NOT_READY")
    conflict = client.put("/v1/desired-mode", json={"mode": "llm"}, headers=AUTH)
    assert (conflict.status_code, conflict.json()["code"]) == (409, "CONFLICT")

    events = client.get("/v1/events", headers=AUTH).json()["events"]
    moves = [(event["from"], event["to"]) for event in events]
    assert moves == [
        ("RECOVERING", "IDLE"),
        ("IDLE", "LOADING_LLM"),
        ("LOADING_LLM", "LLM_READY"),
        ("LLM_READY", "GENERATING_TEXT"),
        ("GENERATING_TEXT", "LLM_READY"),
        ("LLM_READY", "UNLOADING_LLM"),
        ("UNLOADING_LLM", "IDLE"),
    ]
    assert all(is_legal(GpuState(source), GpuState(target)) for source, target in moves)
    assert events[-1]["details"]["exit_code"] == 0
    assert calls(gate, "start") == [("start", ContainerName.LLM)]
    assert calls(gate, "stop") == [("stop", ContainerName.LLM)]
    assert gate.containers[ContainerName.IMAGE].state is ContainerState.CREATED

    assert client.post("/v1/resume", headers=AUTH).status_code == 202
    wait_for_state(client, "LLM_READY")


def test_lease_and_mode_requests_are_validated(client: TestClient) -> None:
    wait_for_state(client, "LLM_READY")
    for body in (
        {"mode": "none", "job_id": "job-1"},
        {"mode": "llm", "job_id": "job 1"},
        {"mode": "llm", "job_id": "job-1", "ttl_s": 0},
        {"mode": "llm", "job_id": "job-1", "prompt": "hello"},
    ):
        assert client.post("/v1/leases", json=body, headers=AUTH).status_code == 422
    assert client.put("/v1/desired-mode", json={"mode": "text"}, headers=AUTH).status_code == 422
    assert client.request("DELETE", "/v1/leases/x", json={"outcome": "fine"}, headers=AUTH).status_code == 422
    assert client.get("/v1/state?wait=26", headers=AUTH).status_code == 422
    assert client.post("/v1/recover", headers=AUTH).status_code == 409
    accepted = client.put("/v1/desired-mode", json={"mode": "none", "job_id": "job-1"}, headers=AUTH)
    assert accepted.status_code == 202
    wait_for_state(client, "IDLE")
