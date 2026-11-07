"""OrchestratorClient: the backend's lease calls to the control API (docs/api.md, Control API).

A lease is the global GPU lock. The job scheduler acquires one before it uses a runtime, renews it every 10 s while the
job runs, and releases it with the job's outcome (docs/gpu-orchestration.md, Leases and the global lock). The client
is blocking, like probe_control_api: call it from a worker thread, never on the event loop. It ignores proxy settings
from the environment, so the token only ever travels to the orchestrator.

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
LeaseOutcome = Literal["ok", "oom", "runtime_error"]
# docs/gpu-orchestration.md: a lease lives 30 s unless a heartbeat renews it, and one is due every 10 s.
DEFAULT_LEASE_TTL_S = 30
HEARTBEAT_INTERVAL_S = 10
REQUEST_TIMEOUT_S = 5.0
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

    def _send(self, method: str, path: str, payload: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        """Returns the status and the JSON object of the answer, {} when the body is not one. Raises
        OrchestratorUnavailableError when no answer arrives within the timeout."""
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
            response = self._opener.open(request, timeout=self._timeout_s)
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


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
