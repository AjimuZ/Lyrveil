"""T-ORC-09, orchestrator side: drain and shutdown through the control API (docs/gpu-orchestration.md, Safe shutdown).

`stop.cmd` drains the backend's queue, then calls POST /v1/drain and waits for IDLE, then stops the containers. These
tests play the orchestrator's part with the application in process over the fake gate and probe: the drain waits for
the current lease, stops the runtime of either mode with its release verified and refuses new leases and modes; resume
applies the mode recorded before; and a shutdown, the end of the application's lifespan, leaves the containers to
`docker compose stop`, after which a new process recovers from what Docker reports. The backend side is
backend/tests/integration/test_shutdown_drain.py.
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
from lyrveil_orchestrator.supervisor import ProcessSupervisor, SupervisorTimings

TOKEN = "ab" * 32
AUTH = {"Authorization": "Bearer " + TOKEN}
FAST = SupervisorTimings(readiness_poll_s=0.01, settle_interval_s=0.01)


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


def serve(gate: FakeContainerGate) -> TestClient:
    """Returns a client of a new orchestrator process over `gate`; entering it runs the lifespan."""
    monitor = GPUResourceMonitor(FakeGpuProbe())
    manager = ModelLifecycleManager(ProcessSupervisor(gate, monitor, FAST), tick_s=0.05)
    return TestClient(create_app(TOKEN, monitor, manager))


@pytest.fixture
def gate() -> FakeContainerGate:
    return FakeContainerGate()


@pytest.fixture
def client(gate: FakeContainerGate) -> Iterator[TestClient]:
    with serve(gate) as test_client:
        yield test_client


def lease_for(client: TestClient, mode: str, job_id: str) -> dict[str, Any]:
    granted = client.post("/v1/leases", json={"mode": mode, "job_id": job_id}, headers=AUTH)
    assert granted.status_code == 201, granted.text
    lease: dict[str, Any] = granted.json()
    return lease


def events(client: TestClient) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = client.get("/v1/events", headers=AUTH).json()["events"]
    return found


def test_the_drain_waits_for_the_lease_then_stops_the_runtime_and_refuses_new_work(
    client: TestClient, gate: FakeContainerGate
) -> None:
    wait_for_state(client, "LLM_READY")
    lease = lease_for(client, "llm", "job-1")

    started = client.post("/v1/drain", headers=AUTH)
    assert (started.status_code, started.json()) == (202, {"draining": True, "state": "GENERATING_TEXT"})
    # The running reply keeps its runtime and its lease until it ends.
    time.sleep(0.3)
    held = client.get("/v1/state", headers=AUTH).json()
    assert (held["state"], held["draining"], held["lease"]["job_id"]) == ("GENERATING_TEXT", True, "job-1")
    assert client.post(f"/v1/leases/{lease['id']}/heartbeat", headers=AUTH).status_code == 200
    assert calls(gate, "stop") == []
    conflict = client.put("/v1/desired-mode", json={"mode": "image", "job_id": "job-2"}, headers=AUTH)
    assert (conflict.status_code, conflict.json()["code"]) == (409, "CONFLICT")

    released = client.request("DELETE", f"/v1/leases/{lease['id']}", json={"outcome": "ok"}, headers=AUTH)
    assert released.json() == {"released": True, "state": "LLM_READY"}
    idle = wait_for_state(client, "IDLE")
    assert (idle["draining"], idle["desired_mode"], idle["lease"]) == (True, "llm", None)
    assert idle["runtimes"]["llm"]["container"] == "exited"
    refused = client.post("/v1/leases", json={"mode": "llm", "job_id": "job-3"}, headers=AUTH)
    assert (refused.status_code, refused.json()["code"]) == (409, "NOT_READY")
    stop, verified = events(client)[-2:]
    assert (stop["from"], stop["to"], stop["reason"], stop["job_id"]) == ("LLM_READY", "UNLOADING_LLM", "drain", None)
    assert (verified["to"], verified["reason"], verified["details"]["exit_code"]) == ("IDLE", "release verified", 0)
    assert calls(gate, "stop") == [("stop", ContainerName.LLM)]

    # Nothing loads while the drain lasts, however long the GPU rests.
    time.sleep(0.3)
    assert client.get("/v1/state", headers=AUTH).json()["state"] == "IDLE"
    assert calls(gate, "start") == [("start", ContainerName.LLM)]
    assert client.post("/v1/resume", headers=AUTH).json() == {"draining": False, "state": "IDLE"}
    wait_for_state(client, "LLM_READY")
    lease_for(client, "llm", "job-4")


def test_the_drain_stops_the_image_runtime_and_resume_applies_the_mode_asked_before(
    client: TestClient, gate: FakeContainerGate
) -> None:
    wait_for_state(client, "LLM_READY")
    assert client.put("/v1/desired-mode", json={"mode": "image", "job_id": "job-7"}, headers=AUTH).status_code == 202
    wait_for_state(client, "IMAGE_READY")

    assert client.post("/v1/drain", headers=AUTH).status_code == 202
    idle = wait_for_state(client, "IDLE")
    assert (idle["desired_mode"], idle["draining"]) == ("image", True)
    assert {name: runtime["container"] for name, runtime in idle["runtimes"].items()} == {
        "llm": "exited",
        "image": "exited",
    }
    stop = events(client)[-2]
    assert (stop["to"], stop["reason"], stop["job_id"]) == ("UNLOADING_IMAGE", "drain", None)

    assert client.post("/v1/resume", headers=AUTH).status_code == 202
    wait_for_state(client, "IMAGE_READY")
    assert calls(gate, "start") == [
        ("start", ContainerName.LLM),
        ("start", ContainerName.IMAGE),
        ("start", ContainerName.IMAGE),
    ]


def test_a_shutdown_leaves_the_containers_and_a_new_process_recovers_without_the_old_lease(
    gate: FakeContainerGate,
) -> None:
    with serve(gate) as before:
        wait_for_state(before, "LLM_READY")
        lease = lease_for(before, "llm", "job-1")
    # The lifespan ended the run loop: no container call was made for the shutdown itself.
    assert calls(gate, "stop") == []
    assert gate.containers[ContainerName.LLM].state is ContainerState.RUNNING

    with serve(gate) as after:
        ready = wait_for_state(after, "LLM_READY")
        assert (ready["draining"], ready["lease"]) == (False, None)
        lost = after.post(f"/v1/leases/{lease['id']}/heartbeat", headers=AUTH)
        assert (lost.status_code, lost.json()["code"]) == (410, "LEASE_EXPIRED")
        # The healthy runtime was kept, not started again.
        assert calls(gate, "start") == [("start", ContainerName.LLM)]
        assert [(event["from"], event["to"]) for event in events(after)] == [("RECOVERING", "LLM_READY")]

        # stop.cmd: the drain, then docker compose stop, then start.cmd brings a new process that is not draining.
        assert after.post("/v1/drain", headers=AUTH).status_code == 202
        wait_for_state(after, "IDLE")
    with serve(gate) as restarted:
        ready = wait_for_state(restarted, "LLM_READY")
        assert ready["draining"] is False
        assert [(event["from"], event["to"]) for event in events(restarted)] == [
            ("RECOVERING", "IDLE"),
            ("IDLE", "LOADING_LLM"),
            ("LOADING_LLM", "LLM_READY"),
        ]
