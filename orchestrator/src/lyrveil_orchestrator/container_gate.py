"""Allowlisted Docker Engine calls on the two runtime containers: the only code that uses the Docker socket.

ADR 0006 and docs/security.md (Docker socket) set the rules, and docs/runtime-contracts.md (Docker Engine API) lists
the calls. The gate sends five of them, pinned to API v1.44: inspect, start, stop, top and logs, for the containers of
ContainerName and no other. It has no generic request method and builds no create, exec, remove, image, volume or
network request: every request comes from one builder, which accepts only the calls of CALLS.

Every method first inspects the container and refuses it unless Docker resolved the exact name and the Compose labels
name the lyrveil project and the matching service, so a foreign container with the same name is never started, stopped
or read. The action that follows addresses the container by name in a second request: a container swapped in between
would go unnoticed, and swapping it takes someone who already controls Docker.

Errors name only allowlisted containers, calls and status codes, never a value read from Docker or passed by the
caller, so they may be logged.
"""

import struct
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType, TracebackType
from typing import Self

import httpx

# The socket that Compose mounts into the orchestrator container, and into no other.
DOCKER_SOCKET = "/var/run/docker.sock"
# Requests and answers keep this version's format whatever the engine. The measured engine accepts 1.40 to 1.56.
API_VERSION = "v1.44"
# The Compose project and the labels that identify its containers.
COMPOSE_PROJECT = "lyrveil"
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
# The log lines a logs call returns, counted from the end: enough to classify a load failure.
LOG_TAIL = 200
# The longest grace period a stop accepts. The documented ones are 20 s for the LLM runtime and 30 s for the image one.
MAX_STOP_GRACE_S = 120

# Docker ignores the host name on its Unix socket, so any fixed name works.
_BASE_URL = "http://docker"
# The media types of a logs answer: multiplexed for a container without a TTY, raw for one with a TTY.
_MULTIPLEXED_STREAM = "application/vnd.docker.multiplexed-stream"
_RAW_STREAM = "application/vnd.docker.raw-stream"
# The header of each frame of a multiplexed stream: the stream type, three zero bytes and the payload size.
_FRAME_HEADER = struct.Struct(">B3xI")
# The stream types of stdout and stderr. Any other type in a logs answer is refused.
_OUTPUT_STREAMS = frozenset({1, 2})


class ContainerName(StrEnum):
    """The allowlist: the only containers the gate acts on, by their Compose container names."""

    LLM = "lyrveil-llm"
    IMAGE = "lyrveil-image"


# The Compose service that the service label of each container must name.
_SERVICES: Mapping[ContainerName, str] = MappingProxyType(
    {ContainerName.LLM: "lyrveil-llm", ContainerName.IMAGE: "lyrveil-image"}
)


class ContainerState(StrEnum):
    """The values of State.Status in an inspect answer."""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    RESTARTING = "restarting"
    REMOVING = "removing"
    EXITED = "exited"
    DEAD = "dead"


# The states in which State.ExitCode belongs to a finished run.
_ENDED_STATES = frozenset({ContainerState.EXITED, ContainerState.DEAD})


class HealthStatus(StrEnum):
    """The values of State.Health.Status for a container with a health check."""

    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class ContainerStatus:
    """What an inspect answer says about one runtime container.

    `running` is State.Running, the flag that release verification trusts. `health` is None for a container without a
    health check. `exit_code` is set once the container has exited or died, and None before.
    """

    name: ContainerName
    state: ContainerState
    running: bool
    health: HealthStatus | None
    exit_code: int | None


@dataclass(frozen=True)
class ProcessTable:
    """The processes in a container as top lists them, one value per title in each row.

    Both tuples are empty when the container is not running.
    """

    titles: tuple[str, ...]
    processes: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class DockerCall:
    """One documented Engine API call.

    `method` and `action`, the path segment after the container name, form the request. `query` holds the only
    parameter names the call takes, and `timeout_s` bounds the wait for the answer. `operation` names the call in
    errors.
    """

    operation: str
    method: str
    action: str
    query: frozenset[str]
    timeout_s: float


INSPECT = DockerCall("inspect", "GET", "json", frozenset(), 10.0)
# A start answers once the process runs, after the hooks of the NVIDIA runtime, so a slow engine gets more time.
START = DockerCall("start", "POST", "start", frozenset(), 60.0)
# A stop answers only once the container has exited. Its request waits the grace period plus this margin, which covers
# the SIGKILL that Docker sends when the grace period ends.
STOP = DockerCall("stop", "POST", "stop", frozenset({"t"}), 30.0)
TOP = DockerCall("top", "GET", "top", frozenset(), 10.0)
LOGS = DockerCall("logs", "GET", "logs", frozenset({"stdout", "stderr", "tail"}), 10.0)

# Every call the gate can build. The builder refuses anything else before a request exists.
CALLS: frozenset[DockerCall] = frozenset({INSPECT, START, STOP, TOP, LOGS})


class ContainerGateError(Exception):
    """Base of every refusal and failure of the gate. Messages hold only allowlisted values, so they may be logged."""


class ContainerNotAllowedError(ContainerGateError):
    """The name is not a ContainerName member. Raised before any request is built."""

    def __init__(self) -> None:
        super().__init__("container name is not in the allowlist")


class EndpointNotAllowedError(ContainerGateError):
    """The call is not one of CALLS, or its query does not name exactly the call's parameters. Raised before any
    request is built."""

    def __init__(self) -> None:
        super().__init__("Docker Engine call is not in the allowlist")


class ContainerCallError(ContainerGateError):
    """A call on an allowlisted container that did not succeed. `operation` names the call that failed."""

    def __init__(self, name: ContainerName, operation: str, detail: str) -> None:
        super().__init__(f"{operation} {name}: {detail}")
        self.name = name
        self.operation = operation
        self.detail = detail


class ForeignContainerError(ContainerCallError):
    """Docker resolved the name to a container that is not Lyrveil's. Nothing was sent after the inspect."""


class ContainerMissingError(ContainerCallError):
    """Docker has no container with the name. The recovery table maps it to RUNTIME_CONTAINER_MISSING."""


class DockerUnavailableError(ContainerCallError):
    """The socket could not be reached, or the answer did not arrive in time. The recovery table maps it to
    DOCKER_UNAVAILABLE."""


class DockerResponseError(ContainerCallError):
    """Docker answered with a status or a body that the call does not document. `status_code` is the HTTP status."""

    def __init__(self, name: ContainerName, operation: str, detail: str, status_code: int) -> None:
        super().__init__(name, operation, detail)
        self.status_code = status_code


class ContainerGate:
    """Inspects, starts, stops and reads the two runtime containers through the Docker socket, and does nothing else.

    Each of the five calls takes a ContainerName member and raises ContainerNotAllowedError for anything else before a
    request exists. Each then inspects the container and raises ForeignContainerError when it is not Lyrveil's,
    ContainerMissingError when it does not exist, DockerUnavailableError when Docker cannot be reached in time, and
    DockerResponseError for an answer the call does not document. Calls may run concurrently; the caller, the process
    supervisor, decides their order. Close the gate with aclose, or use it as an async context manager.
    """

    def __init__(self) -> None:
        # No proxy or certificate setting from the environment applies, and an answer cannot redirect the client to
        # another path.
        transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET, trust_env=False)
        self._client = httpx.AsyncClient(
            transport=transport, base_url=_BASE_URL, trust_env=False, follow_redirects=False
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Closes the connections to the socket. The gate must not be used afterwards."""
        await self._client.aclose()

    async def inspect(self, name: ContainerName) -> ContainerStatus:
        """Returns the state, health and exit code of the container, once its identity is checked."""
        container = _allowed(name)
        return _status(container, await self._identified(container, INSPECT))

    async def start(self, name: ContainerName) -> bool:
        """Starts the pre-created container. Returns True when Docker started it, False when it was already running."""
        container = _allowed(name)
        await self._identified(container, START)
        return _changed(container, START, await self._send(START, container))

    async def stop(self, name: ContainerName, grace_s: int) -> bool:
        """Sends SIGTERM, then SIGKILL after `grace_s` seconds, and returns once the container has exited.

        Returns True when Docker stopped it, False when it was not running. Raises ValueError, before any request,
        unless `grace_s` is an int from 1 to MAX_STOP_GRACE_S.
        """
        container = _allowed(name)
        grace = _grace(grace_s)
        await self._identified(container, STOP)
        response = await self._send(STOP, container, {"t": str(grace)}, timeout_s=grace + STOP.timeout_s)
        return _changed(container, STOP, response)

    async def top(self, name: ContainerName) -> ProcessTable:
        """Returns the processes running in the container, or an empty table when it is not running."""
        container = _allowed(name)
        await self._identified(container, TOP)
        response = await self._send(TOP, container)
        # Docker answers 409 for a container that is not running, which has no process to report.
        if response.status_code == httpx.codes.CONFLICT:
            return ProcessTable((), ())
        return _processes(container, _json(container, TOP, response))

    async def logs(self, name: ContainerName) -> str:
        """Returns the last LOG_TAIL lines of stdout and stderr together, in the order Docker received them.

        The text comes from a runtime and may echo private input: callers classify it and never store or log it.
        """
        container = _allowed(name)
        await self._identified(container, LOGS)
        response = await self._send(LOGS, container, {"stdout": "1", "stderr": "1", "tail": str(LOG_TAIL)})
        _expect(container, LOGS, response, httpx.codes.OK)
        return _log_text(container, response)

    async def _identified(self, container: ContainerName, call: DockerCall) -> Mapping[str, object]:
        """Returns the inspect document of the container once its identity is checked. `call` names refusals."""
        response = await self._send(INSPECT, container)
        document = _json(container, INSPECT, response)
        _check_identity(container, call.operation, document)
        return document

    async def _send(
        self,
        call: DockerCall,
        container: ContainerName,
        query: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> httpx.Response:
        """Sends a documented call and returns the answer, read in full.

        Raises what _request raises, DockerUnavailableError when the socket cannot be reached or the answer does not
        arrive in time, and ContainerMissingError when Docker answers 404.
        """
        request = self._request(call, container, query, timeout_s)
        try:
            response = await self._client.send(request)
        except httpx.TransportError as error:
            raise DockerUnavailableError(container, call.operation, "Docker Engine did not answer") from error
        if response.status_code == httpx.codes.NOT_FOUND:
            raise ContainerMissingError(container, call.operation, "no such container")
        return response

    def _request(
        self,
        call: DockerCall,
        container: ContainerName,
        query: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> httpx.Request:
        """Builds the request of a documented call. It is the only place where the gate builds one.

        The path holds only the API version, the container's enum value and the call's action, so it is always one of
        the documented paths. Raises EndpointNotAllowedError when `call` is not one of CALLS or `query` does not name
        exactly the call's parameters, and ContainerNotAllowedError when `container` is not a ContainerName member.
        `timeout_s` replaces the call's own wait for the answer.
        """
        if call not in CALLS:
            raise EndpointNotAllowedError()
        name = _allowed(container)
        params = dict(query or {})
        if frozenset(params) != call.query:
            raise EndpointNotAllowedError()
        return self._client.build_request(
            call.method,
            f"/{API_VERSION}/containers/{name.value}/{call.action}",
            params=params or None,
            timeout=call.timeout_s if timeout_s is None else timeout_s,
        )


def _allowed(name: object) -> ContainerName:
    """Returns `name` when it is a ContainerName member, or raises ContainerNotAllowedError.

    A plain string is refused even when it spells an allowed name, so every caller goes through the enum.
    """
    if not isinstance(name, ContainerName):
        raise ContainerNotAllowedError()
    return name


def _grace(grace_s: object) -> int:
    """Returns `grace_s` when it is an int from 1 to MAX_STOP_GRACE_S, or raises ValueError."""
    # bool is a subclass of int, and True must not pass for one second.
    if isinstance(grace_s, bool) or not isinstance(grace_s, int) or not 1 <= grace_s <= MAX_STOP_GRACE_S:
        raise ValueError(f"the stop grace period must be an int from 1 to {MAX_STOP_GRACE_S} seconds")
    return grace_s


def _check_identity(container: ContainerName, operation: str, document: Mapping[str, object]) -> None:
    """Raises ForeignContainerError unless the inspect document describes Lyrveil's container `container`."""
    # Docker reports the name with a leading slash. Comparing it confirms which container answered.
    if document.get("Name") != f"/{container.value}":
        raise ForeignContainerError(container, operation, "identity check failed on the name")
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise ForeignContainerError(container, operation, "identity check failed: no labels")
    if labels.get(PROJECT_LABEL) != COMPOSE_PROJECT:
        raise ForeignContainerError(container, operation, f"identity check failed on {PROJECT_LABEL}")
    if labels.get(SERVICE_LABEL) != _SERVICES[container]:
        raise ForeignContainerError(container, operation, f"identity check failed on {SERVICE_LABEL}")


def _status(container: ContainerName, document: Mapping[str, object]) -> ContainerStatus:
    """Reads State from an inspect document. Raises DockerResponseError when a field is missing or unknown."""
    state = document.get("State")
    if not isinstance(state, dict):
        raise DockerResponseError(container, INSPECT.operation, "State is missing", httpx.codes.OK)
    docker_state = _member(ContainerState, state.get("Status"))
    running = state.get("Running")
    exit_code = state.get("ExitCode")
    # bool is a subclass of int, so an exit code of True or False is refused explicitly.
    if (
        docker_state is None
        or not isinstance(running, bool)
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
    ):
        raise DockerResponseError(container, INSPECT.operation, "State is malformed", httpx.codes.OK)
    return ContainerStatus(
        name=container,
        state=docker_state,
        running=running,
        health=_health(container, state.get("Health")),
        exit_code=exit_code if docker_state in _ENDED_STATES else None,
    )


def _health(container: ContainerName, health: object) -> HealthStatus | None:
    """Reads State.Health: None for a container without a health check, or when Docker reports "none"."""
    if health is None:
        return None
    status = health.get("Status") if isinstance(health, dict) else None
    if status == "none":
        return None
    member = _member(HealthStatus, status)
    if member is None:
        raise DockerResponseError(container, INSPECT.operation, "State.Health is malformed", httpx.codes.OK)
    return member


def _member[E: StrEnum](kind: type[E], value: object) -> E | None:
    """Returns the member of `kind` whose value is `value`, or None for any other value."""
    if not isinstance(value, str):
        return None
    try:
        return kind(value)
    except ValueError:
        return None


def _json(container: ContainerName, call: DockerCall, response: httpx.Response) -> Mapping[str, object]:
    """Returns the JSON object of a 200 answer, or raises DockerResponseError for any other status or body."""
    _expect(container, call, response, httpx.codes.OK)
    try:
        body = response.json()
    except ValueError as error:
        raise DockerResponseError(container, call.operation, "answer is not JSON", response.status_code) from error
    if not isinstance(body, dict):
        raise DockerResponseError(container, call.operation, "answer is not a JSON object", response.status_code)
    return body


def _expect(container: ContainerName, call: DockerCall, response: httpx.Response, status: int) -> None:
    """Raises DockerResponseError unless the answer has `status`."""
    if response.status_code != status:
        raise DockerResponseError(container, call.operation, "unexpected status", response.status_code)


def _changed(container: ContainerName, call: DockerCall, response: httpx.Response) -> bool:
    """Reads the answer to a start or a stop: True for 204, and False for 304, which Docker sends when the container
    is already in the requested state. Raises DockerResponseError for any other status."""
    if response.status_code == httpx.codes.NOT_MODIFIED:
        return False
    _expect(container, call, response, httpx.codes.NO_CONTENT)
    return True


def _processes(container: ContainerName, body: Mapping[str, object]) -> ProcessTable:
    """Reads a top answer. Raises DockerResponseError when the titles or a row are not lists of strings of one size."""
    # Docker encodes an empty list as null.
    titles_value = body.get("Titles")
    rows = body.get("Processes")
    titles = _strings([] if titles_value is None else titles_value)
    rows = [] if rows is None else rows
    if titles is None or not isinstance(rows, list):
        raise DockerResponseError(container, TOP.operation, "answer is malformed", httpx.codes.OK)
    processes: list[tuple[str, ...]] = []
    for row in rows:
        values = _strings(row)
        if values is None or len(values) != len(titles):
            raise DockerResponseError(container, TOP.operation, "a process does not match the titles", httpx.codes.OK)
        processes.append(values)
    return ProcessTable(titles, tuple(processes))


def _strings(value: object) -> tuple[str, ...] | None:
    """Returns a JSON list of strings as a tuple, or None for any other value."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return None


def _log_text(container: ContainerName, response: httpx.Response) -> str:
    """Returns the text of a logs answer: the stdout and stderr payloads of a multiplexed stream, or the body of a raw
    one. Invalid UTF-8 is replaced, since callers only match the text against patterns. Raises DockerResponseError for
    another media type or a malformed stream."""
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type == _MULTIPLEXED_STREAM:
        output = _demultiplex(container, response.content)
    elif media_type == _RAW_STREAM:
        output = response.content
    else:
        raise DockerResponseError(container, LOGS.operation, "unknown log stream format", response.status_code)
    return output.decode("utf-8", errors="replace")


def _demultiplex(container: ContainerName, stream: bytes) -> bytes:
    """Joins the payloads of a multiplexed stream in order. Raises DockerResponseError for a truncated frame or for a
    stream other than stdout and stderr."""
    output = bytearray()
    offset = 0
    while offset < len(stream):
        if len(stream) - offset < _FRAME_HEADER.size:
            raise DockerResponseError(container, LOGS.operation, "truncated log frame", httpx.codes.OK)
        kind, size = _FRAME_HEADER.unpack_from(stream, offset)
        start = offset + _FRAME_HEADER.size
        offset = start + size
        if kind not in _OUTPUT_STREAMS or offset > len(stream):
            raise DockerResponseError(container, LOGS.operation, "malformed log frame", httpx.codes.OK)
        output += stream[start:offset]
    return bytes(output)
