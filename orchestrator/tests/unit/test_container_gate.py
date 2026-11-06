"""T-ORC-07: the container gate acts only on the two runtime containers, after checking their Compose labels, and can
build no request outside the documented Docker Engine calls."""

import ast
import asyncio
import json
import re
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from lyrveil_orchestrator import container_gate
from lyrveil_orchestrator.container_gate import (
    CALLS,
    INSPECT,
    LOGS,
    START,
    STOP,
    TOP,
    ContainerGate,
    ContainerMissingError,
    ContainerName,
    ContainerNotAllowedError,
    ContainerState,
    ContainerStatus,
    DockerCall,
    DockerResponseError,
    DockerUnavailableError,
    EndpointNotAllowedError,
    ForeignContainerError,
    HealthStatus,
    ProcessTable,
)

LLM = ContainerName.LLM
IMAGE = ContainerName.IMAGE
BASE_URL = "http://docker"
PROJECT = "com.docker.compose.project"
SERVICE = "com.docker.compose.service"
MULTIPLEXED = "application/vnd.docker.multiplexed-stream"
RAW = "application/vnd.docker.raw-stream"

# The calls of docs/runtime-contracts.md (Docker Engine API), written out again so that a change to the gate must
# change this too.
DOCUMENTED = {
    (method, f"/v1.44/containers/{name}/{action}")
    for name in ("lyrveil-llm", "lyrveil-image")
    for method, action in (("GET", "json"), ("POST", "start"), ("POST", "stop"), ("GET", "top"), ("GET", "logs"))
}
# What the gate must never reach: remove (DELETE), create, exec, images, volumes, networks, and the other endpoint
# families that docs/security.md excludes.
FORBIDDEN_METHODS = {"DELETE", "PUT", "PATCH", "HEAD"}
FORBIDDEN_PATH = re.compile(
    r"/(?:containers/create|exec|attach|commit|archive|export|kill|update|rename|pause|unpause|wait|resize|prune"
    r"|images|build|volumes|networks|system|plugins|swarm|nodes|services|tasks|secrets|configs|session|distribution"
    r"|auth|info|version|events)\b"
)
# HTTPX functions that send a request without the gate's client.
HTTPX_SHORTCUTS = {"delete", "get", "head", "options", "patch", "post", "put", "request", "stream"}
# The HTTPX classes that make a client or a transport that reaches the network. The gate makes one client over one
# transport, so a second way to send requests cannot appear unnoticed.
HTTPX_CONNECTORS = {"AsyncClient", "AsyncHTTPTransport", "Client", "HTTPTransport"}
# What the gate may import: HTTPX as a module, so that the checks below see each use of it, and standard modules that
# open no connection. Every request therefore goes through HTTPX.
GATE_IMPORTS = {"collections.abc", "dataclasses", "enum", "httpx", "struct", "types", "typing"}


def forbidden(method: str, target: str) -> bool:
    """Returns True for a request to an endpoint that the gate must never reach."""
    return method in FORBIDDEN_METHODS or FORBIDDEN_PATH.search(target) is not None


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    """Runs the async tests on asyncio, the event loop of the orchestrator."""
    return "asyncio"


def path(name: ContainerName, action: str) -> str:
    """The documented path of `action` on the container `name`."""
    return f"/v1.44/containers/{name.value}/{action}"


def answer(name: ContainerName, state: dict[str, object] | None = None) -> dict[str, Any]:
    """An inspect answer of Lyrveil's container `name`, running and healthy unless `state` replaces State."""
    return {
        "Id": "f" * 64,
        "Name": f"/{name.value}",
        "Config": {
            "Labels": {PROJECT: "lyrveil", SERVICE: name.value, "com.docker.compose.oneoff": "False"},
            "Tty": False,
        },
        "State": state or {"Status": "running", "Running": True, "ExitCode": 0, "Health": {"Status": "healthy"}},
    }


def relabeled(name: ContainerName, change: dict[str, str | None]) -> dict[str, Any]:
    """The inspect answer of `name` with `change` applied to its labels. A None value removes the label."""
    result = answer(name)
    labels: dict[str, str] = result["Config"]["Labels"]
    for key, value in change.items():
        if value is None:
            del labels[key]
        else:
            labels[key] = value
    return result


def other(name: ContainerName) -> ContainerName:
    """The other runtime container."""
    return IMAGE if name is LLM else LLM


def frame(stream: int, payload: bytes) -> bytes:
    """One frame of Docker's multiplexed log stream."""
    return struct.pack(">B3xI", stream, len(payload)) + payload


def stub() -> respx.MockRouter:
    """A respx router for the base URL of the socket. A request that matches no route fails the test.

    The router forgets its calls when its `with` block ends, so assertions on them belong inside the block.
    """
    return respx.mock(base_url=BASE_URL, assert_all_called=False, assert_all_mocked=True)


def route_documented(router: respx.MockRouter, name: ContainerName, inspect: dict[str, Any] | None = None) -> None:
    """Routes every documented call on `name` to a successful answer. `inspect` replaces the inspect answer."""
    router.get(path(name, "json")).respond(200, json=answer(name) if inspect is None else inspect)
    router.post(path(name, "start")).respond(204)
    router.post(path(name, "stop")).respond(204)
    router.get(path(name, "top")).respond(200, json={"Titles": ["PID", "CMD"], "Processes": [["1", "tini"]]})
    router.get(path(name, "logs")).respond(200, content=frame(1, b"ready\n"), headers={"Content-Type": MULTIPLEXED})


def sent(router: respx.MockRouter) -> list[tuple[str, str]]:
    """The method and path of every request the router received, in order."""
    return [(call.request.method, call.request.url.path) for call in router.calls]


def failing(kind: type[httpx.TransportError]) -> Callable[[httpx.Request], httpx.Response]:
    """A respx side effect that raises a new `kind` for each request, as the transport would."""

    def fail(request: httpx.Request) -> httpx.Response:
        raise kind("the engine did not answer", request=request)

    return fail


def gate_tree() -> ast.Module:
    """The syntax tree of container_gate.py."""
    return ast.parse(Path(container_gate.__file__).read_text(encoding="utf-8"))


# Every public call of the gate, with valid arguments apart from the name.
OPERATIONS: dict[str, Callable[[ContainerGate, Any], Awaitable[object]]] = {
    "inspect": lambda gate, name: gate.inspect(name),
    "start": lambda gate, name: gate.start(name),
    "stop": lambda gate, name: gate.stop(name, 20),
    "top": lambda gate, name: gate.top(name),
    "logs": lambda gate, name: gate.logs(name),
}

# Names that the gate refuses: plain strings, even those that spell an allowed name, the other Lyrveil services,
# look-alikes, paths that would leave the container's path, a container ID, and values that are not strings.
REFUSED_NAMES: list[object] = [
    "lyrveil-llm",
    "lyrveil-image",
    "lyrveil-backend",
    "lyrveil-orchestrator",
    "lyrveil-frontend",
    "lyrveil-models",
    "lyrveil-llm-1",
    "LYRVEIL-LLM",
    " lyrveil-llm",
    "../images/json",
    "lyrveil-llm/../../images/create",
    "lyrveil-llm?force=1",
    "f" * 64,
    "",
    None,
    1,
    b"lyrveil-llm",
]

# Inspect answers of a container that holds the allowlisted name but is not Lyrveil's, by what differs.
FOREIGN: dict[str, Callable[[ContainerName], dict[str, Any]]] = {
    "another project": lambda name: relabeled(name, {PROJECT: "other-project"}),
    "the project in another case": lambda name: relabeled(name, {PROJECT: "Lyrveil"}),
    "no project label": lambda name: relabeled(name, {PROJECT: None}),
    "the other runtime's service": lambda name: relabeled(name, {SERVICE: other(name).value}),
    "another service": lambda name: relabeled(name, {SERVICE: "lyrveil-backend"}),
    "no service label": lambda name: relabeled(name, {SERVICE: None}),
    "no labels": lambda name: {**answer(name), "Config": {"Labels": None}},
    "another name": lambda name: {**answer(name), "Name": "/lyrveil-backend"},
}


def test_the_allowlist_holds_exactly_the_two_runtime_containers() -> None:
    assert {name.value for name in ContainerName} == {"lyrveil-llm", "lyrveil-image"}


def test_the_gate_offers_the_five_documented_calls_and_no_other_method() -> None:
    assert {name for name in dir(ContainerGate) if not name.startswith("_")} == {*OPERATIONS, "aclose"}


@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("POST", "/v1.44/containers/create"),
        ("DELETE", "/v1.44/containers/lyrveil-llm"),
        ("POST", "/v1.44/containers/lyrveil-llm/exec"),
        ("POST", "/v1.44/exec/0123abcd/start"),
        ("POST", "/v1.44/containers/lyrveil-llm/attach"),
        ("POST", "/v1.44/containers/lyrveil-llm/kill"),
        ("POST", "/v1.44/containers/prune"),
        ("POST", "/v1.44/images/create"),
        ("GET", "/v1.44/images/json"),
        ("POST", "/v1.44/build"),
        ("POST", "/v1.44/volumes/create"),
        ("DELETE", "/v1.44/volumes/lyrveil-data"),
        ("POST", "/v1.44/networks/create"),
        ("POST", "/v1.44/networks/lyrveil-runtime/connect"),
    ],
)
def test_the_check_recognizes_each_endpoint_family_the_gate_must_not_reach(method: str, target: str) -> None:
    assert forbidden(method, target)


def test_no_documented_call_is_forbidden() -> None:
    assert not any(forbidden(method, target) for method, target in DOCUMENTED)


@pytest.mark.anyio
async def test_every_request_the_gate_can_build_is_a_documented_call() -> None:
    # The builder accepts only the calls of CALLS and the members of ContainerName, as the tests below show, so these
    # are all the requests it can build. Query values never change the path.
    queries: dict[DockerCall, dict[str, str]] = {
        INSPECT: {},
        START: {},
        STOP: {"t": "20"},
        TOP: {},
        LOGS: {"stdout": "1", "stderr": "1", "tail": "200"},
    }
    assert set(queries) == CALLS
    built: set[tuple[str, str]] = set()
    async with ContainerGate() as gate:
        for call in CALLS:
            for name in ContainerName:
                request = gate._request(call, name, queries[call])
                built.add((request.method, request.url.path))
    assert built == DOCUMENTED
    assert not any(forbidden(method, target) for method, target in built)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "call",
    [
        DockerCall("remove", "DELETE", "json", frozenset(), 10.0),
        DockerCall("create", "POST", "create", frozenset(), 10.0),
        DockerCall("exec", "POST", "exec", frozenset(), 10.0),
        DockerCall("kill", "POST", "kill", frozenset(), 10.0),
        DockerCall("images", "GET", "../../images/json", frozenset(), 10.0),
        DockerCall("volumes", "POST", "../../volumes/create", frozenset(), 10.0),
        DockerCall("networks", "POST", "../../networks/create", frozenset(), 10.0),
        DockerCall("start", "POST", "start", frozenset({"detachKeys"}), 60.0),
        DockerCall("start", "PUT", "start", frozenset(), 60.0),
    ],
    ids=lambda call: f"{call.method} {call.action}",
)
async def test_a_call_outside_the_documented_list_is_refused_before_a_request_exists(call: DockerCall) -> None:
    query = dict.fromkeys(call.query, "1")
    with stub() as router:
        async with ContainerGate() as gate:
            with pytest.raises(EndpointNotAllowedError):
                gate._request(call, LLM, query)
            with pytest.raises(EndpointNotAllowedError):
                await gate._send(call, LLM, query)
        assert sent(router) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("call", "query"),
    [
        (INSPECT, {"size": "1"}),
        (START, {"detachKeys": "ctrl-p"}),
        (STOP, {}),
        (STOP, {"t": "20", "signal": "SIGKILL"}),
        (TOP, {"ps_args": "aux"}),
        (LOGS, {"stdout": "1", "stderr": "1"}),
        (LOGS, {"stdout": "1", "stderr": "1", "tail": "200", "follow": "1"}),
    ],
)
async def test_a_query_that_the_call_does_not_document_is_refused(call: DockerCall, query: dict[str, str]) -> None:
    async with ContainerGate() as gate:
        with pytest.raises(EndpointNotAllowedError):
            gate._request(call, LLM, query)


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["lyrveil-llm", "lyrveil-backend", "../images/json", None])
async def test_the_builder_refuses_a_name_that_is_not_an_allowlist_member(name: object) -> None:
    async with ContainerGate() as gate:
        with pytest.raises(ContainerNotAllowedError):
            gate._request(INSPECT, cast(ContainerName, name))


@pytest.mark.anyio
@pytest.mark.parametrize("name", REFUSED_NAMES, ids=repr)
@pytest.mark.parametrize("operation", sorted(OPERATIONS))
async def test_a_name_outside_the_allowlist_is_refused_before_any_request(operation: str, name: object) -> None:
    with stub() as router:
        async with ContainerGate() as gate:
            with pytest.raises(ContainerNotAllowedError):
                await OPERATIONS[operation](gate, name)
        assert sent(router) == []


@pytest.mark.anyio
@pytest.mark.parametrize("name", list(ContainerName))
@pytest.mark.parametrize("variant", sorted(FOREIGN))
@pytest.mark.parametrize("operation", sorted(OPERATIONS))
async def test_a_container_that_is_not_lyrveils_is_refused_after_the_inspect(
    operation: str, variant: str, name: ContainerName
) -> None:
    with stub() as router:
        route_documented(router, name, inspect=FOREIGN[variant](name))
        async with ContainerGate() as gate:
            with pytest.raises(ForeignContainerError) as refused:
                await OPERATIONS[operation](gate, name)
        assert sent(router) == [("GET", path(name, "json"))]
    assert refused.value.operation == operation


@pytest.mark.anyio
@pytest.mark.parametrize("name", list(ContainerName))
async def test_each_call_inspects_first_and_sends_only_documented_requests(name: ContainerName) -> None:
    with stub() as router:
        route_documented(router, name)
        async with ContainerGate() as gate:
            for operation in OPERATIONS.values():
                await operation(gate, name)
        requests = sent(router)
    inspect = ("GET", path(name, "json"))
    assert requests == [
        inspect,
        inspect,
        ("POST", path(name, "start")),
        inspect,
        ("POST", path(name, "stop")),
        inspect,
        ("GET", path(name, "top")),
        inspect,
        ("GET", path(name, "logs")),
    ]
    assert set(requests) <= DOCUMENTED


@pytest.mark.anyio
@pytest.mark.parametrize("grace", [1, 20, 30, 120])
async def test_a_stop_sends_its_grace_period_and_waits_longer_than_it(grace: int) -> None:
    with stub() as router:
        route_documented(router, LLM)
        async with ContainerGate() as gate:
            assert await gate.stop(LLM, grace) is True
        request = router.calls.last.request
    assert dict(request.url.params) == {"t": str(grace)}
    assert request.extensions["timeout"]["read"] > grace


@pytest.mark.anyio
@pytest.mark.parametrize("grace", [0, -1, 121, 3600, True, 1.5, "20", None])
async def test_a_stop_grace_period_outside_1_to_120_seconds_is_refused_before_any_request(grace: object) -> None:
    with stub() as router:
        async with ContainerGate() as gate:
            with pytest.raises(ValueError, match="grace period"):
                await gate.stop(LLM, cast(int, grace))
        assert sent(router) == []


@pytest.mark.anyio
async def test_logs_ask_for_the_documented_tail_of_both_streams_and_never_follow() -> None:
    with stub() as router:
        route_documented(router, IMAGE)
        async with ContainerGate() as gate:
            await gate.logs(IMAGE)
        assert dict(router.calls.last.request.url.params) == {"stdout": "1", "stderr": "1", "tail": "200"}


@pytest.mark.anyio
@pytest.mark.parametrize(("status", "changed"), [(204, True), (304, False)])
async def test_start_and_stop_report_whether_docker_changed_the_container(status: int, changed: bool) -> None:
    with stub() as router:
        router.get(path(LLM, "json")).respond(200, json=answer(LLM))
        router.post(path(LLM, "start")).respond(status)
        router.post(path(LLM, "stop")).respond(status)
        async with ContainerGate() as gate:
            assert await gate.start(LLM) is changed
            assert await gate.stop(LLM, 20) is changed


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            {"Status": "running", "Running": True, "ExitCode": 0, "Health": {"Status": "healthy"}},
            ContainerStatus(LLM, ContainerState.RUNNING, True, HealthStatus.HEALTHY, None),
        ),
        (
            {"Status": "running", "Running": True, "ExitCode": 0, "Health": {"Status": "starting"}},
            ContainerStatus(LLM, ContainerState.RUNNING, True, HealthStatus.STARTING, None),
        ),
        (
            {"Status": "exited", "Running": False, "ExitCode": 3, "Health": {"Status": "unhealthy"}},
            ContainerStatus(LLM, ContainerState.EXITED, False, HealthStatus.UNHEALTHY, 3),
        ),
        (
            {"Status": "exited", "Running": False, "ExitCode": 0, "Health": {"Status": "none"}},
            ContainerStatus(LLM, ContainerState.EXITED, False, None, 0),
        ),
        (
            {"Status": "created", "Running": False, "ExitCode": 0},
            ContainerStatus(LLM, ContainerState.CREATED, False, None, None),
        ),
    ],
)
async def test_inspect_reports_state_health_and_exit_code(state: dict[str, object], expected: ContainerStatus) -> None:
    with stub() as router:
        router.get(path(LLM, "json")).respond(200, json=answer(LLM, state))
        async with ContainerGate() as gate:
            assert await gate.inspect(LLM) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [
        {"Status": "zombie", "Running": False, "ExitCode": 0},
        {"Status": "running", "Running": "yes", "ExitCode": 0},
        {"Status": "exited", "Running": False, "ExitCode": True},
        {"Status": "exited", "Running": False},
        {"Status": "running", "Running": True, "ExitCode": 0, "Health": {"Status": "sick"}},
        {"Status": "running", "Running": True, "ExitCode": 0, "Health": "healthy"},
    ],
)
async def test_an_inspect_answer_with_a_malformed_state_is_refused(state: dict[str, object]) -> None:
    with stub() as router:
        router.get(path(LLM, "json")).respond(200, json=answer(LLM, state))
        async with ContainerGate() as gate:
            with pytest.raises(DockerResponseError):
                await gate.inspect(LLM)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "content"),
    [(200, b"{not json"), (200, b'["not", "an", "object"]'), (500, b'{"message": "server error"}'), (301, b"")],
)
@pytest.mark.parametrize("operation", sorted(OPERATIONS))
async def test_an_inspect_answer_that_the_call_does_not_document_stops_the_call(
    operation: str, status: int, content: bytes
) -> None:
    # A redirect is not followed either: following it would request a path that has no route here.
    with stub() as router:
        router.get(path(LLM, "json")).respond(status, content=content, headers={"Location": "/v1.44/images/json"})
        async with ContainerGate() as gate:
            with pytest.raises(DockerResponseError) as failed:
                await OPERATIONS[operation](gate, LLM)
        assert sent(router) == [("GET", path(LLM, "json"))]
    assert failed.value.status_code == status


@pytest.mark.anyio
@pytest.mark.parametrize("operation", sorted(OPERATIONS))
async def test_a_missing_container_is_reported_without_further_requests(operation: str) -> None:
    with stub() as router:
        router.get(path(IMAGE, "json")).respond(404, json={"message": "No such container: lyrveil-image"})
        async with ContainerGate() as gate:
            with pytest.raises(ContainerMissingError):
                await OPERATIONS[operation](gate, IMAGE)
        assert sent(router) == [("GET", path(IMAGE, "json"))]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError], ids=lambda kind: kind.__name__
)
@pytest.mark.parametrize("operation", sorted(OPERATIONS))
async def test_an_engine_that_does_not_answer_is_reported_unavailable(
    operation: str, failure: type[httpx.TransportError]
) -> None:
    with stub() as router:
        router.get(path(LLM, "json")).mock(side_effect=failing(failure))
        async with ContainerGate() as gate:
            with pytest.raises(DockerUnavailableError):
                await OPERATIONS[operation](gate, LLM)


@pytest.mark.anyio
async def test_errors_carry_no_text_from_docker() -> None:
    marker = "private-marker-5b1e"
    with stub() as router:
        router.get(path(LLM, "json")).respond(200, json=answer(LLM))
        router.post(path(LLM, "start")).respond(500, json={"message": marker})
        router.get(path(IMAGE, "json")).respond(200, json=relabeled(IMAGE, {PROJECT: marker}))
        async with ContainerGate() as gate:
            with pytest.raises(DockerResponseError) as failed:
                await gate.start(LLM)
            with pytest.raises(ForeignContainerError) as refused:
                await gate.stop(IMAGE, 30)
    assert failed.value.status_code == 500
    assert marker not in str(failed.value)
    assert str(refused.value) == "stop lyrveil-image: identity check failed on com.docker.compose.project"


@pytest.mark.anyio
async def test_top_lists_the_processes_and_none_for_a_container_that_is_not_running() -> None:
    table = {"Titles": ["PID", "CMD"], "Processes": [["1", "tini"], ["7", "koboldcpp"]]}
    not_running = httpx.Response(409, json={"message": "container is not running"})
    with stub() as router:
        router.get(path(LLM, "json")).respond(200, json=answer(LLM))
        router.get(path(LLM, "top")).mock(side_effect=[httpx.Response(200, json=table), not_running])
        async with ContainerGate() as gate:
            assert await gate.top(LLM) == ProcessTable(("PID", "CMD"), (("1", "tini"), ("7", "koboldcpp")))
            assert await gate.top(LLM) == ProcessTable((), ())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"Titles": ["PID", "CMD"], "Processes": [["1"]]},
        {"Titles": ["PID", "CMD"], "Processes": [["1", 7]]},
        {"Titles": "PID CMD", "Processes": []},
        {"Titles": ["PID"], "Processes": "1"},
    ],
)
async def test_a_top_answer_whose_rows_do_not_match_the_titles_is_refused(body: dict[str, object]) -> None:
    with stub() as router:
        router.get(path(LLM, "json")).respond(200, json=answer(LLM))
        router.get(path(LLM, "top")).respond(200, json=body)
        async with ContainerGate() as gate:
            with pytest.raises(DockerResponseError):
                await gate.top(LLM)


@pytest.mark.anyio
async def test_logs_join_stdout_and_stderr_in_the_order_docker_sent_them() -> None:
    stream = frame(1, b"loading model\n") + frame(2, b"CUDA out of memory\n") + frame(1, b"caff\xc3\xa8 \xff\n")
    with stub() as router:
        router.get(path(LLM, "json")).respond(200, json=answer(LLM))
        router.get(path(LLM, "logs")).respond(200, content=stream, headers={"Content-Type": MULTIPLEXED})
        async with ContainerGate() as gate:
            assert await gate.logs(LLM) == "loading model\nCUDA out of memory\ncaffè �\n"


@pytest.mark.anyio
async def test_logs_of_a_container_with_a_tty_are_returned_as_sent() -> None:
    with stub() as router:
        router.get(path(LLM, "json")).respond(200, json=answer(LLM))
        router.get(path(LLM, "logs")).respond(200, content=b"progress\r\ndone\n", headers={"Content-Type": RAW})
        async with ContainerGate() as gate:
            assert await gate.logs(LLM) == "progress\r\ndone\n"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (frame(1, b"ready\n")[:5], MULTIPLEXED),
        (struct.pack(">B3xI", 1, 10) + b"short", MULTIPLEXED),
        (frame(3, b"error from the engine\n"), MULTIPLEXED),
        (frame(0, b"stdin\n"), MULTIPLEXED),
        (b"ready\n", "text/plain"),
    ],
    ids=["truncated header", "truncated payload", "system error stream", "stdin stream", "another media type"],
)
async def test_a_log_stream_that_is_malformed_or_of_another_format_is_refused(content: bytes, media_type: str) -> None:
    with stub() as router:
        router.get(path(LLM, "json")).respond(200, json=answer(LLM))
        router.get(path(LLM, "logs")).respond(200, content=content, headers={"Content-Type": media_type})
        async with ContainerGate() as gate:
            with pytest.raises(DockerResponseError):
                await gate.logs(LLM)


@pytest.mark.anyio
async def test_the_gate_speaks_http_over_the_unix_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A real socket instead of a stub: it shows what reaches the other end of DOCKER_SOCKET.
    body = json.dumps(answer(LLM)).encode()
    request_lines: list[bytes] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        request_lines.append(head.split(b"\r\n", 1)[0])
        header = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        writer.write(header.encode() + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    socket_path = tmp_path / "docker.sock"
    monkeypatch.setattr(container_gate, "DOCKER_SOCKET", str(socket_path))
    server = await asyncio.start_unix_server(serve, path=socket_path)
    async with server, ContainerGate() as gate:
        status = await gate.inspect(LLM)
    assert request_lines == [b"GET /v1.44/containers/lyrveil-llm/json HTTP/1.1"]
    assert status == ContainerStatus(LLM, ContainerState.RUNNING, True, HealthStatus.HEALTHY, None)


def test_the_gate_module_names_no_forbidden_endpoint_or_method() -> None:
    # A bare segment such as "exec" counts as well as a path. Prose in docstrings has no slash before its words.
    texts = [
        node.value for node in ast.walk(gate_tree()) if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert [text for text in texts if FORBIDDEN_PATH.search(text) or FORBIDDEN_PATH.search(f"/{text}")] == []
    assert [text for text in texts if text.upper() in FORBIDDEN_METHODS] == []
    assert {"GET", "POST"} <= set(texts)


def test_the_gate_reaches_its_http_client_through_one_builder_and_one_sender() -> None:
    tree = gate_tree()
    client_calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_client"
    ]
    assert sorted(client_calls) == ["aclose", "build_request", "send"]
    httpx_names = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "httpx"
    }
    assert httpx_names.isdisjoint(HTTPX_SHORTCUTS)
    connectors = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "httpx"
        and node.func.attr in HTTPX_CONNECTORS
    ]
    assert sorted(connectors) == ["AsyncClient", "AsyncHTTPTransport"]


def test_the_gate_imports_only_httpx_and_standard_modules_that_open_no_connection() -> None:
    imported: set[str] = set()
    for node in ast.walk(gate_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports are not expected"
            assert node.module != "httpx", "the gate uses HTTPX through its module name only"
            imported.add(node.module or "")
    assert "httpx" in imported
    assert imported <= GATE_IMPORTS
