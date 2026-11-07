"""OrchestratorClient: the backend's lease and mode calls to the control API (docs/api.md, Control API).

A lease is the global GPU lock. The job scheduler acquires one before it uses a runtime, renews it every 10 s while the
job runs, and releases it with the job's outcome (docs/gpu-orchestration.md, Leases and the global lock). It reads the
GPU state and asks for the mode its queue needs with PUT /v1/desired-mode, and GpuStateFollower long-polls the state
with `wait_for_state` to announce each change on the event WebSocket. Since P06.5 LifecycleEventRecorder reads the
event log with `read_events`, and the `drain-gpu` and `resume` commands of lyrveil.cli call `drain` and `resume`. The
client is blocking, like probe_control_api: call it from a worker thread, never on the event loop. It ignores proxy
settings from the environment, so the token only ever travels to the orchestrator.

Errors never carry the token. They may carry the job id, the lease id and status codes, which hold no user data.
"""

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
class LoadFailure:
    """The orchestrator's last failed load since that runtime was last ready (docs/gpu-orchestration.md, Load failures
    and launch levels).

    `seq` is the event of the failure, so the backend acts once on each. `oom` marks an allocation failure of the text
    runtime, after which the backend lowers the launch level before the retry. `failures` counts the failed loads in a
    row, and `final` is True when no retry follows: the text runtime waits in ERROR, and after the image runtime the
    GPU returns to mode `llm`, so the backend ends `job_id`, the job the image mode was asked for.
    """

    seq: int
    mode: LeaseMode
    code: str
    oom: bool
    failures: int
    final: bool
    job_id: str | None


@dataclass(frozen=True)
class GpuStatus:
    """The part of the state document the backend acts on. `state` is the GPU state name, `desired_mode` the mode
    the orchestrator is moving to, and `draining` whether it refuses new modes.

    `seq` counts the orchestrator's events since the process named `boot_id` started: its transitions, and the start
    and end of a load held back for VRAM. `blocked_reason` is the code that holds a load back, such as
    GPU_INSUFFICIENT_VRAM, and `blocked_vram` the numbers of that hold. `load_failure` is the last failed load. Each is
    None when the answer did not carry it.
    """

    state: str
    desired_mode: DesiredMode
    draining: bool
    seq: int | None = None
    boot_id: str | None = None
    blocked_reason: str | None = None
    blocked_vram: BlockedVram | None = None
    load_failure: LoadFailure | None = None

    @property
    def ready_mode(self) -> LeaseMode | None:
        """Returns the mode whose runtime is ready, or None while the GPU is loading, unloading, idle or recovering."""
        return _READY_STATES.get(self.state)


@dataclass(frozen=True)
class LifecycleEvent:
    """One event of GET /v1/events: a transition from `source` to `target`, or a note in one state (a hold for VRAM).

    `seq` counts the events of the orchestrator process that sent it, from 1. `at` is aware UTC. `code` is the failure
    that caused it, if any, and `details` holds only numbers: VRAM values in MiB, exit codes and load counts.
    """

    seq: int
    at: datetime
    source: str
    target: str
    reason: str
    job_id: str | None
    code: str | None
    details: Mapping[str, int | None] = field(default_factory=dict)


@dataclass(frozen=True)
class EventPage:
    """An answer of GET /v1/events: the `boot_id` of the orchestrator process, and its buffered events after the
    requested `seq`, oldest first, or all of them when the requested boot was another process's."""

    boot_id: str
    events: tuple[LifecycleEvent, ...]


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
        """Reads the state document at `path`. `seq`, `boot_id`, `blocked_reason`, `blocked_vram` and `load_failure`
        are None when absent or of another form; the other fields must have their documented types, or
        OrchestratorUnavailableError is raised."""
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
            load_failure=_load_failure(body.get("load_failure")),
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

    def read_events(self, boot_id: str | None, after: int) -> EventPage:
        """Returns the orchestrator's buffered events with `seq` above `after`, or all of them when `boot_id` is not
        the current process's: GET /v1/events. An event that is not of the documented form is left out.

        Raises OrchestratorUnavailableError for anything but a 200 with a `boot_id` and a list of events. `after` must
        be at least 0.
        """
        query = urllib.parse.urlencode({"after": after} if boot_id is None else {"boot_id": boot_id, "after": after})
        status, body = self._send("GET", f"/v1/events?{query}", None)
        current, events = body.get("boot_id"), body.get("events")
        if status != 200 or not isinstance(current, str) or not isinstance(events, list):
            raise OrchestratorUnavailableError(f"the control API answered {status} to events without events")
        read = (_event(item) for item in events)
        return EventPage(boot_id=current, events=tuple(event for event in read if event is not None))

    def drain(self) -> GpuStatus:
        """Starts the drain, POST /v1/drain: the orchestrator stops the runtime once the current lease ends, and
        refuses new leases and modes until `resume`. Returns at once with the state read afterwards; poll it for IDLE.
        Raises OrchestratorUnavailableError for anything but a 202."""
        self._accepted("/v1/drain", "drain")
        return self.read_state()

    def resume(self) -> None:
        """Leaves the drain, POST /v1/resume: the desired mode recorded before it applies again. Raises
        OrchestratorUnavailableError for anything but a 202."""
        self._accepted("/v1/resume", "resume")

    def _accepted(self, path: str, operation: str) -> None:
        status, _ = self._send("POST", path, None)
        if status != 202:
            raise OrchestratorUnavailableError(f"the control API answered {status} to {operation}")

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


def _load_failure(value: object) -> LoadFailure | None:
    """Reads `load_failure` of the state document, or returns None when it is absent or not of the documented form."""
    if not isinstance(value, dict):
        return None
    seq, failures = _mib(value.get("seq")), _mib(value.get("failures"))
    mode, code = value.get("mode"), value.get("code")
    oom, final, job_id = value.get("oom"), value.get("final"), value.get("job_id")
    if (
        seq is None
        or mode not in ("llm", "image")
        or not isinstance(code, str)
        or failures is None
        or not isinstance(oom, bool)
        or not isinstance(final, bool)
        or not (job_id is None or isinstance(job_id, str))
    ):
        return None
    return LoadFailure(seq=seq, mode=mode, code=code, oom=oom, failures=failures, final=final, job_id=job_id)


def _optional_text(value: object) -> tuple[bool, str | None]:
    """Returns (True, value) for a string or None, and (False, None) for anything else."""
    return (True, value) if value is None or isinstance(value, str) else (False, None)


def _event(value: object) -> LifecycleEvent | None:
    """Reads one event of GET /v1/events, or returns None when it is not of the documented form. Detail values other
    than integers and null are left out, since `details` holds only numbers."""
    if not isinstance(value, dict):
        return None
    seq, at, source, target, reason = (value.get(key) for key in ("seq", "at", "from", "to", "reason"))
    job_ok, job_id = _optional_text(value.get("job_id"))
    code_ok, code = _optional_text(value.get("code"))
    details = value.get("details", {})
    if (
        isinstance(seq, bool)
        or not isinstance(seq, int)
        or seq < 1
        or not isinstance(at, str)
        or not isinstance(source, str)
        or not isinstance(target, str)
        or not isinstance(reason, str)
        or not job_ok
        or not code_ok
        or not isinstance(details, dict)
    ):
        return None
    try:
        moment = datetime.fromisoformat(at)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return LifecycleEvent(
        seq=seq,
        at=moment.astimezone(UTC),
        source=source,
        target=target,
        reason=reason,
        job_id=job_id,
        code=code,
        details={
            str(key): item
            for key, item in details.items()
            if item is None or (isinstance(item, int) and not isinstance(item, bool))
        },
    )


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
