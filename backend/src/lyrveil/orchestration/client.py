"""OrchestratorClient: the backend's lease and mode calls to the control API (docs/api.md, Control API).

A lease is the global GPU lock. The job scheduler acquires one before it uses a runtime, renews it every 10 s while the
job runs, and releases it with the job's outcome (docs/gpu-orchestration.md, Leases and the global lock). It reads the
GPU state and asks for the mode its queue needs with PUT /v1/desired-mode, and GpuStateFollower long-polls the state
with `wait_for_state` to announce each change on the event WebSocket. The client is blocking, like
probe_control_api: call it from a worker thread, never on the event loop. It ignores proxy settings from the
environment, so the token only ever travels to the orchestrator.

Errors never carry the token. They may carry the job id, the lease id and status codes, which hold no user data.
"""

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import SecretStr

from lyrveil.orchestration.health import CONTROL_API_URL

LeaseMode = Literal["llm", "image"]
DesiredMode = Literal["llm", "image", "none"]
LeaseOutcome = Literal["ok", "oom", "runtime_error"]
# The GPU states in which each runtime is ready and serves leases of its mode (docs/gpu-orchestration.md).
_READY_STATES: dict[str, LeaseMode] = {
    "LLM_READY": "llm",
    "GENERATING_TEXT": "llm",
    "IMAGE_READY": "image",
    "GENERATING_IMAGE": "image",
}
# docs/gpu-orchestration.md: a lease lives 30 s unless a heartbeat renews it, and one is due every 10 s.
DEFAULT_LEASE_TTL_S = 30
HEARTBEAT_INTERVAL_S = 10
REQUEST_TIMEOUT_S = 5.0
# The longest long poll GET /v1/state accepts (docs/api.md, Control API).
MAX_STATE_WAIT_S = 25.0
# The refusals of POST /v1/leases. Both mean "not now": the job stays queued.
REFUSAL_CODES = frozenset({"NOT_READY", "LEASE_HELD"})


@dataclass(frozen=True)
class Lease:
    """A lease the orchestrator granted. `expires_in_s` is the time it had left when the answer was sent."""

    id: str
    mode: LeaseMode
    job_id: str
    ttl_s: int
    expires_in_s: float


@dataclass(frozen=True)
class BlockedVram:
    """The orchestrator's last VRAM check of a held-back load: the runtime of `mode` needs `required_mib` free and
    the card had `free_mib`, the rest being held by other programs."""

    mode: LeaseMode
    free_mib: int
    required_mib: int


@dataclass(frozen=True)
class GpuStatus:
    """The part of the state document the backend acts on. `state` is the GPU state name, `desired_mode` the mode
    the orchestrator is moving to, and `draining` whether it refuses new modes.

    `seq` counts the orchestrator's events since the process named `boot_id` started: its transitions, and the start
    and end of a load held back for VRAM. `blocked_reason` is the code that holds a load back, such as
    GPU_INSUFFICIENT_VRAM, and `blocked_vram` the numbers of that hold. Each is None when the answer did not carry it.
    """

    state: str
    desired_mode: DesiredMode
    draining: bool
    seq: int | None = None
    boot_id: str | None = None
    blocked_reason: str | None = None
    blocked_vram: BlockedVram | None = None

    @property
    def ready_mode(self) -> LeaseMode | None:
        """Returns the mode whose runtime is ready, or None while the GPU is loading, unloading, idle or recovering."""
        return _READY_STATES.get(self.state)


class OrchestratorError(Exception):
    """Base of every failure of the client."""


class OrchestratorUnavailableError(OrchestratorError):
    """The control API did not answer, refused the token, or sent an answer the contract does not document. The API
    reports it as ORCHESTRATOR_UNAVAILABLE."""


class LeaseRefusedError(OrchestratorError):
    """No lease was granted. `code` is NOT_READY, when the GPU is not in the mode yet, or LEASE_HELD."""

    def __init__(self, code: str) -> None:
        super().__init__(f"lease refused: {code}")
        self.code = code


class ModeRefusedError(OrchestratorError):
    """PUT /v1/desired-mode answered 409 CONFLICT: the orchestrator is draining and accepts no new mode."""


class LeaseLostError(OrchestratorError):
    """The lease expired, was ended with the runtime, or the orchestrator restarted. The job retries with LEASE_LOST
    and must not touch the runtime again under this lease."""

    def __init__(self, lease_id: str) -> None:
        super().__init__(f"lease {lease_id} lost")
        self.lease_id = lease_id


class OrchestratorClient:
    """Acquires, renews and releases leases on the control API at `base_url`, with `token` as the bearer token."""

    def __init__(self, token: SecretStr, base_url: str = CONTROL_API_URL, timeout_s: float = REQUEST_TIMEOUT_S) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read_state(self) -> GpuStatus:
        """Returns the current GPU state, without waiting. Raises OrchestratorUnavailableError for anything but a 200
        whose state, desired mode and drain flag have the documented types."""
        return self._state("/v1/state", self._timeout_s)

    def wait_for_state(self, since: int, wait_s: float) -> GpuStatus:
        """Returns the GPU state once its `seq` exceeds `since`, or after `wait_s` seconds, whichever comes first: the
        long poll of GET /v1/state. `wait_s` is held to 0 to MAX_STATE_WAIT_S, and 0 answers at once.

        Raises OrchestratorUnavailableError as read_state does, and also for an answer without `seq` and `boot_id`,
        which a follower of the state needs. `since` must be at least 0.
        """
        wait = min(max(wait_s, 0.0), MAX_STATE_WAIT_S)
        status = self._state(f"/v1/state?since={since}&wait={wait:g}", self._timeout_s + wait)
        if status.seq is None or status.boot_id is None:
            raise OrchestratorUnavailableError("the control API sent a state without seq or boot_id")
        return status

    def _state(self, path: str, timeout_s: float) -> GpuStatus:
        """Reads the state document at `path`. `seq`, `boot_id`, `blocked_reason` and `blocked_vram` are None when
        absent or of another type; the other fields must have their documented types, or OrchestratorUnavailableError
        is raised."""
        status, body = self._send("GET", path, None, timeout_s)
        state, desired, draining = body.get("state"), body.get("desired_mode"), body.get("draining")
        if (
            status != 200
            or not isinstance(state, str)
            or desired not in ("llm", "image", "none")
            or not isinstance(draining, bool)
        ):
            raise OrchestratorUnavailableError(f"the control API answered {status} to state without a state")
        seq, boot_id, blocked = body.get("seq"), body.get("boot_id"), body.get("blocked_reason")
        return GpuStatus(
            state=state,
            desired_mode=desired,
            draining=draining,
            seq=seq if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0 else None,
            boot_id=boot_id if isinstance(boot_id, str) else None,
            blocked_reason=blocked if isinstance(blocked, str) else None,
            blocked_vram=_blocked_vram(body.get("blocked_vram")),
        )

    def request_mode(self, mode: DesiredMode, job_id: str | None = None) -> None:
        """Asks the orchestrator to move to `mode` once no lease is active, on behalf of `job_id` when given. Raises
        ModeRefusedError for a 409 CONFLICT, while draining, and OrchestratorUnavailableError for anything else that is
        not a 202."""
        payload: dict[str, Any] = {"mode": mode} if job_id is None else {"mode": mode, "job_id": job_id}
        status, body = self._send("PUT", "/v1/desired-mode", payload)
        if status == 202:
            return
        if status == 409 and body.get("code") == "CONFLICT":
            raise ModeRefusedError(f"mode {mode} refused: the orchestrator is draining")
        raise OrchestratorUnavailableError(f"the control API answered {status} to desired-mode")

    def acquire_lease(self, mode: LeaseMode, job_id: str, ttl_s: int = DEFAULT_LEASE_TTL_S) -> Lease:
        """Returns the granted lease. Raises LeaseRefusedError for a 409 NOT_READY or LEASE_HELD, and
        OrchestratorUnavailableError for anything else that is not a 201 with a lease."""
        status, body = self._send("POST", "/v1/leases", {"mode": mode, "job_id": job_id, "ttl_s": ttl_s})
        if status == 409 and body.get("code") in REFUSAL_CODES:
            raise LeaseRefusedError(str(body["code"]))
        return self._lease(status, 201, body, "acquire")

    def renew_lease(self, lease: Lease) -> Lease:
        """Sends the heartbeat and returns the lease with its new time left. Raises LeaseLostError for a 410, and
        OrchestratorUnavailableError for anything else that is not a 200 with the lease."""
        status, body = self._send("POST", f"/v1/leases/{lease.id}/heartbeat", None)
        if status == 410:
            raise LeaseLostError(lease.id)
        return self._lease(status, 200, body, "heartbeat")

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        """Ends the lease with the job's outcome. Returns True when the orchestrator ended it, False for a 410: it
        had already expired or been lost, so nothing is left to release. Raises OrchestratorUnavailableError for any
        other answer."""
        status, _ = self._send("DELETE", f"/v1/leases/{lease.id}", {"outcome": outcome})
        if status == 200:
            return True
        if status == 410:
            return False
        raise OrchestratorUnavailableError(f"the control API answered {status} to release")

    def _send(
        self, method: str, path: str, payload: dict[str, Any] | None, timeout_s: float | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Returns the status and the JSON object of the answer, {} when the body is not one. Raises
        OrchestratorUnavailableError when no answer arrives within `timeout_s`, by default the client's timeout."""
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token.get_secret_value()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        unanswered = OrchestratorUnavailableError(f"the control API did not answer {method} {path}")
        try:
            response = self._opener.open(request, timeout=self._timeout_s if timeout_s is None else timeout_s)
        except urllib.error.HTTPError as error:
            # A 4xx or 5xx answer is still an answer: its status and body are read like a success's.
            response = error
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            raise unanswered from error
        try:
            with response:
                return int(response.status), _json_object(response.read())
        except (http.client.HTTPException, OSError) as error:
            raise unanswered from error

    @staticmethod
    def _lease(status: int, expected: int, body: dict[str, Any], operation: str) -> Lease:
        """Reads the lease of a successful answer. Raises OrchestratorUnavailableError for another status or a body
        that is not a lease."""
        if status != expected:
            raise OrchestratorUnavailableError(f"the control API answered {status} to {operation}")
        lease_id, mode, job_id = body.get("id"), body.get("mode"), body.get("job_id")
        ttl_s, expires_in_s = body.get("ttl_s"), body.get("expires_in_s")
        if (
            not isinstance(lease_id, str)
            or mode not in ("llm", "image")
            or not isinstance(job_id, str)
            or isinstance(ttl_s, bool)
            or not isinstance(ttl_s, int)
            or isinstance(expires_in_s, bool)
            or not isinstance(expires_in_s, int | float)
        ):
            raise OrchestratorUnavailableError(f"the control API sent a malformed lease to {operation}")
        return Lease(id=lease_id, mode=mode, job_id=job_id, ttl_s=ttl_s, expires_in_s=float(expires_in_s))


def _mib(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _blocked_vram(value: object) -> BlockedVram | None:
    """Reads `blocked_vram` of the state document, or returns None when it is absent or not of the documented form."""
    if not isinstance(value, dict):
        return None
    mode, free, required = value.get("mode"), _mib(value.get("free_mib")), _mib(value.get("required_mib"))
    if mode not in ("llm", "image") or free is None or required is None:
        return None
    return BlockedVram(mode=mode, free_mib=free, required_mib=required)


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
