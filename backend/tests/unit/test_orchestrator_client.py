"""Tests OrchestratorClient's lease, state and mode calls against a local HTTP server that answers like the control
API."""

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any, ClassVar

import pytest
from pydantic import SecretStr

from lyrveil.orchestration.client import (
    BlockedVram,
    Lease,
    LeaseLostError,
    LeaseRefusedError,
    ModeRefusedError,
    OrchestratorClient,
    OrchestratorUnavailableError,
)

TOKEN = "ab" * 32
LEASE_ID = "0192f3a0-0000-7000-8000-000000000000"
LEASE_BODY = {"id": LEASE_ID, "mode": "llm", "job_id": "job-1", "ttl_s": 30, "expires_in_s": 30.0}


class ControlApi(BaseHTTPRequestHandler):
    """Answers each request with the next scripted (status, body) and records (method, path, auth, body)."""

    answers: ClassVar[list[tuple[int, Any]]] = []
    requests: ClassVar[list[tuple[str, str, str | None, Any]]] = []

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.requests.append(
            (self.command, self.path, self.headers.get("Authorization"), json.loads(raw) if raw else None)
        )
        status, body = self.answers.pop(0)
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        self._answer()

    def do_DELETE(self) -> None:
        self._answer()

    def do_GET(self) -> None:
        self._answer()

    def do_PUT(self) -> None:
        self._answer()

    def log_message(self, format: str, *args: object) -> None:
        """Keeps the test output quiet."""


@pytest.fixture
def server() -> Iterator[HTTPServer]:
    ControlApi.answers = []
    ControlApi.requests = []
    httpd = HTTPServer(("127.0.0.1", 0), ControlApi)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join()


@pytest.fixture
def client(server: HTTPServer) -> OrchestratorClient:
    return OrchestratorClient(SecretStr(TOKEN), f"http://127.0.0.1:{server.server_port}", timeout_s=2.0)


def test_a_lease_is_acquired_renewed_and_released(client: OrchestratorClient) -> None:
    ControlApi.answers = [(201, LEASE_BODY), (200, {**LEASE_BODY, "expires_in_s": 29.5}), (200, {"released": True})]
    lease = client.acquire_lease("llm", "job-1")
    assert lease == Lease(LEASE_ID, "llm", "job-1", 30, 30.0)
    assert client.renew_lease(lease).expires_in_s == 29.5
    assert client.release_lease(lease, "oom") is True
    assert ControlApi.requests == [
        ("POST", "/v1/leases", "Bearer " + TOKEN, {"mode": "llm", "job_id": "job-1", "ttl_s": 30}),
        ("POST", f"/v1/leases/{LEASE_ID}/heartbeat", "Bearer " + TOKEN, None),
        ("DELETE", f"/v1/leases/{LEASE_ID}", "Bearer " + TOKEN, {"outcome": "oom"}),
    ]


@pytest.mark.parametrize("code", ["NOT_READY", "LEASE_HELD"])
def test_a_refused_lease_reports_the_reason(client: OrchestratorClient, code: str) -> None:
    ControlApi.answers = [(409, {"status": 409, "code": code})]
    with pytest.raises(LeaseRefusedError) as refused:
        client.acquire_lease("llm", "job-1")
    assert refused.value.code == code


def test_an_expired_lease_is_lost_on_heartbeat_and_has_nothing_to_release(client: OrchestratorClient) -> None:
    ControlApi.answers = [(410, {"code": "LEASE_EXPIRED"}), (410, {"code": "LEASE_EXPIRED"})]
    lease = Lease(LEASE_ID, "llm", "job-1", 30, 30.0)
    with pytest.raises(LeaseLostError):
        client.renew_lease(lease)
    assert client.release_lease(lease) is False


@pytest.mark.parametrize(
    "answer",
    [
        (401, {"code": "UNAUTHORIZED"}),
        (409, {"code": "CONFLICT"}),
        (500, b"not json"),
        (201, {**LEASE_BODY, "ttl_s": "30"}),
        (201, [LEASE_BODY]),
    ],
)
def test_an_undocumented_answer_is_unavailable_and_never_shows_the_token(
    client: OrchestratorClient, answer: tuple[int, Any]
) -> None:
    ControlApi.answers = [answer]
    with pytest.raises(OrchestratorUnavailableError) as failed:
        client.acquire_lease("llm", "job-1")
    assert TOKEN not in str(failed.value)


def test_the_state_is_read_and_a_mode_is_requested(client: OrchestratorClient) -> None:
    ControlApi.answers = [
        (200, {"state": "IMAGE_READY", "desired_mode": "image", "draining": False, "seq": 9, "lease": None}),
        (200, {"state": "UNLOADING_LLM", "desired_mode": "image", "draining": False}),
        (202, {"desired_mode": "llm", "state": "GENERATING_IMAGE"}),
        (202, {"desired_mode": "image", "state": "LLM_READY"}),
    ]
    status = client.read_state()
    assert (status.state, status.desired_mode, status.draining, status.ready_mode) == (
        "IMAGE_READY",
        "image",
        False,
        "image",
    )
    assert client.read_state().ready_mode is None
    client.request_mode("llm")
    client.request_mode("image", "job-1")
    assert [request[:2] for request in ControlApi.requests] == [
        ("GET", "/v1/state"),
        ("GET", "/v1/state"),
        ("PUT", "/v1/desired-mode"),
        ("PUT", "/v1/desired-mode"),
    ]
    assert [request[3] for request in ControlApi.requests[2:]] == [
        {"mode": "llm"},
        {"mode": "image", "job_id": "job-1"},
    ]


def test_the_state_is_long_polled_with_its_sequence_and_blocked_reason(client: OrchestratorClient) -> None:
    blocked = {
        "state": "IDLE",
        "desired_mode": "image",
        "draining": False,
        "seq": 12,
        "boot_id": "orchestrator-1",
        "blocked_reason": "GPU_INSUFFICIENT_VRAM",
    }
    ControlApi.answers = [
        (200, blocked),
        (200, {**blocked, "blocked_reason": None}),
        (200, {"state": "IDLE", "desired_mode": "image", "draining": False, "seq": 12}),
    ]
    status = client.wait_for_state(11, 25.0)
    assert (status.state, status.seq, status.boot_id, status.blocked_reason) == (
        "IDLE",
        12,
        "orchestrator-1",
        "GPU_INSUFFICIENT_VRAM",
    )
    assert client.wait_for_state(0, 0.0).blocked_reason is None
    # A follower cannot go on without the sequence, so an answer without it is not a state.
    with pytest.raises(OrchestratorUnavailableError):
        client.wait_for_state(12, 60.0)
    assert [request[1] for request in ControlApi.requests] == [
        "/v1/state?since=11&wait=25",
        "/v1/state?since=0&wait=0",
        "/v1/state?since=12&wait=25",
    ]


def test_the_numbers_of_a_vram_hold_are_read_and_a_malformed_hold_is_ignored(client: OrchestratorClient) -> None:
    held = {
        "state": "IDLE",
        "desired_mode": "image",
        "draining": False,
        "blocked_reason": "GPU_INSUFFICIENT_VRAM",
        "blocked_vram": {"mode": "image", "free_mib": 5282, "required_mib": 6500},
    }
    ControlApi.answers = [
        (200, held),
        (200, {**held, "blocked_vram": {"mode": "image", "free_mib": "5282", "required_mib": 6500}}),
        (200, {**held, "blocked_vram": {"mode": "none", "free_mib": 5282, "required_mib": 6500}}),
    ]
    assert client.read_state().blocked_vram == BlockedVram(mode="image", free_mib=5282, required_mib=6500)
    assert client.read_state().blocked_vram is None
    assert client.read_state().blocked_vram is None


def test_a_mode_refused_while_draining_and_a_malformed_state_are_reported(client: OrchestratorClient) -> None:
    ControlApi.answers = [
        (409, {"status": 409, "code": "CONFLICT"}),
        (500, {"code": "INTERNAL"}),
        (200, {"state": "LLM_READY", "desired_mode": "text", "draining": False}),
        (200, {"state": "LLM_READY", "desired_mode": "llm"}),
    ]
    with pytest.raises(ModeRefusedError):
        client.request_mode("image", "job-1")
    with pytest.raises(OrchestratorUnavailableError):
        client.request_mode("image")
    for _ in range(2):
        with pytest.raises(OrchestratorUnavailableError):
            client.read_state()


def test_an_unreachable_orchestrator_is_unavailable(server: HTTPServer) -> None:
    base = f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    client = OrchestratorClient(SecretStr(TOKEN), base, timeout_s=0.5)
    with pytest.raises(OrchestratorUnavailableError):
        client.acquire_lease("llm", "job-1")
