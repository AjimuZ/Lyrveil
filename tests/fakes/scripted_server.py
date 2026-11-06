"""Base of the HTTP fakes: a recording server on the standard library whose replies a test can script per endpoint.

Only the standard library is used, so the HTTP fakes run in the small `tests/fakes/Dockerfile` image of
compose.test.yaml, without the orchestrator's packages.

A fake serves exactly the routes in its ROUTES and nothing else: the control surface is Python (`script`) or the
FAKE_SCRIPT environment variable, never an extra endpoint, so what a client can reach is what docs/runtime-contracts.md
lists. An unknown path answers 404, and a known path with another method answers 405.
"""

import base64
import hashlib
import json
import os
import re
import select
import socket
import struct
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import ClassVar, Literal, Self
from urllib.parse import parse_qs, urlsplit

Action = Literal["reply", "invalid_json", "oom", "crash"]

# The exit status of a fake container that crashes: the 128 + 9 of a process that SIGKILL ended, as an OOM kill reports.
CRASH_EXIT_CODE = 137
# RFC 6455, section 1.3: the constant that the Sec-WebSocket-Accept hash includes.
_WEBSOCKET_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
# Inside a container the fake must listen on every interface. Only its own Compose network reaches it.
_ALL_INTERFACES = "0.0.0.0"


@dataclass(frozen=True)
class Step:
    """One scripted behavior, used once by the next request to the endpoint it is scripted on.

    `action` is `reply` (the endpoint's normal answer, or `body` with `status` when `body` is set), `invalid_json` (a
    200 answer whose JSON is cut short), `oom` (the endpoint's out-of-memory failure) or `crash` (the connection drops
    without an answer and the fake stops serving, as a runtime that died would). `delay_s` waits before anything is
    sent. `chunk_delay_s` waits between the chunks of a stream or the messages of a ComfyUI job. `after_chunks` is the
    number of chunks or messages a `crash` lets through first.
    """

    action: Action = "reply"
    status: int = 200
    body: object = None
    delay_s: float = 0.0
    chunk_delay_s: float = 0.0
    after_chunks: int = 0


@dataclass(frozen=True)
class Route:
    """One endpoint: `method` is GET, POST or WS, and `pattern` is the path with `{name}` for a variable segment."""

    method: str
    pattern: str

    @property
    def key(self) -> str:
        return f"{self.method} {self.pattern}"

    def matches(self, path: str) -> bool:
        # re.escape turns "{name}" into "\{name\}", which the substitution then replaces by one path segment.
        regex = re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(self.pattern))
        return re.fullmatch(regex, path) is not None


@dataclass(frozen=True)
class Request:
    """What a client sent: the matched route, the path, the decoded query, and the body.

    `body` is the parsed JSON, or None when the body was empty or not JSON. `raw` is the body as received.
    """

    route: Route
    path: str
    query: dict[str, list[str]]
    body: object
    raw: bytes


@dataclass
class Reply:
    """What the fake sends. A stream (`stream`) has no length and flushes each chunk, then the connection closes."""

    status: int
    chunks: Iterable[bytes]
    content_type: str = "application/json"
    stream: bool = False
    chunk_delay_s: float = 0.0


def json_reply(status: int, body: object) -> Reply:
    """Returns a JSON answer."""
    return Reply(status, [json.dumps(body).encode("utf-8")])


class WebSocket:
    """The server side of one WebSocket connection, enough for text frames to a client that sends nothing useful."""

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        if len(payload) < 126:
            header = struct.pack(">BB", 0x81, len(payload))
        elif len(payload) < 65536:
            header = struct.pack(">BBH", 0x81, 126, len(payload))
        else:
            header = struct.pack(">BBQ", 0x81, 127, len(payload))
        self._connection.sendall(header + payload)

    def peer_closed(self) -> bool:
        """Returns True when the client closed the connection or sent a close frame. Never blocks."""
        readable, _, _ = select.select([self._connection], [], [], 0)
        if not readable:
            return False
        try:
            data = self._connection.recv(4096)
        except OSError:
            return True
        return data == b"" or (data[0] & 0x0F) == 0x8


class ScriptedServer:
    """A recording HTTP server with scriptable replies. Subclasses set ROUTES and implement `build` or `websocket`.

    Start it with `start()` or as a context manager: it listens on `host` and `port` (0 picks a free one, then see
    `port`) in background threads. `requests` lists every request that matched a route, in order. `crashed` becomes
    True after a `crash` step. By default a crash only stops the fake from listening; the container entry point passes
    `on_crash` to end the process instead.
    """

    ROUTES: ClassVar[tuple[Route, ...]] = ()

    def __init__(self, host: str = "127.0.0.1", port: int = 0, on_crash: Callable[[], None] | None = None) -> None:
        self._steps: dict[str, deque[Step]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._on_crash = on_crash
        self._stopping = threading.Event()
        self.requests: list[Request] = []
        self.crashed = False
        self._httpd = _Httpd((host, port), _Handler)
        self._httpd.fake = self
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def start(self) -> Self:
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stops listening and tells the open streams to end. Safe to call more than once."""
        if self._stopping.is_set():
            return
        self._stopping.set()
        if self._thread is not None:
            self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.stop()

    def script(self, route: str, *steps: Step) -> None:
        """Queues `steps` for `route`, such as "POST /api/v1/generate". Raises ValueError for a route the fake lacks."""
        if route not in {candidate.key for candidate in self.ROUTES}:
            raise ValueError(f"this fake has no route {route!r}")
        with self._lock:
            self._steps[route].extend(steps)

    def load_script(self, text: str) -> None:
        """Queues the steps of a JSON object that maps routes to lists of Step fields. Raises ValueError otherwise."""
        document = json.loads(text)
        if not isinstance(document, dict):
            raise ValueError("a script is a JSON object of routes")
        for route, steps in document.items():
            if not isinstance(steps, list):
                raise ValueError(f"the steps of {route!r} are a list")
            self.script(route, *(Step(**step) for step in steps))

    def next_step(self, route: Route) -> Step | None:
        """Returns the next scripted step of `route` and consumes it, or None when the script is empty."""
        with self._lock:
            queue = self._steps[route.key]
            return queue.popleft() if queue else None

    def crash(self) -> None:
        """Marks the fake as crashed and ends it: the process when `on_crash` is set, the listening socket otherwise."""
        self.crashed = True
        if self._on_crash is not None:
            self._on_crash()
            return
        threading.Thread(target=self.stop, daemon=True).start()

    def build(self, request: Request, step: Step | None) -> Reply:
        """Returns the answer to a GET or POST request, following `step` when it is not None. Never a `crash` step."""
        raise NotImplementedError

    def streaming(self, route: Route) -> bool:
        """Returns True for a route whose answer is a stream, where a `crash` step may cut the answer short."""
        return False

    def websocket(self, request: Request, connection: WebSocket) -> None:
        """Serves one WebSocket connection until the client leaves or the fake stops."""
        raise NotImplementedError

    def sleep(self, seconds: float) -> None:
        """Waits `seconds`, ending early when the fake stops."""
        if seconds > 0:
            self._stopping.wait(seconds)

    def dispatch(self, handler: BaseHTTPRequestHandler) -> None:
        """Answers one request: routes it, records it, and applies the next scripted step of its route."""
        url = urlsplit(handler.path)
        is_websocket = handler.headers.get("Upgrade", "").lower() == "websocket"
        method = "WS" if is_websocket else handler.command
        route = next((r for r in self.ROUTES if r.method == method and r.matches(url.path)), None)
        if route is None:
            known = any(candidate.matches(url.path) for candidate in self.ROUTES)
            status, detail = (405, "Method Not Allowed") if known else (404, "Not Found")
            _send(handler, json_reply(status, {"detail": detail}))
            return
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        try:
            body: object = json.loads(raw) if raw else None
        except ValueError:
            body = None
        request = Request(route, url.path, parse_qs(url.query), body, raw)
        self.requests.append(request)
        if route.method == "WS":
            self._serve_websocket(handler, request)
            return
        step = self.next_step(route)
        if step is not None:
            self.sleep(step.delay_s)
        if step is not None and step.action == "crash":
            if step.after_chunks and self.streaming(route):
                _send(handler, self.build(request, replace(step, action="reply")), stop_after=step.after_chunks)
            _drop(handler)
            self.crash()
            return
        _send(handler, self.build(request, step))

    def _serve_websocket(self, handler: BaseHTTPRequestHandler, request: Request) -> None:
        key = handler.headers.get("Sec-WebSocket-Key")
        if key is None:
            _send(handler, json_reply(400, {"detail": "Missing Sec-WebSocket-Key"}))
            return
        accept = base64.b64encode(hashlib.sha1(key.encode("ascii") + _WEBSOCKET_GUID).digest()).decode("ascii")
        lines = [
            "HTTP/1.1 101 Switching Protocols",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Accept: {accept}",
        ]
        handler.wfile.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        handler.wfile.flush()
        handler.close_connection = True
        # The client may leave in the middle of a message.
        with suppress(OSError):
            self.websocket(request, WebSocket(handler.connection))


class _Httpd(ThreadingHTTPServer):
    daemon_threads = True
    fake: ScriptedServer


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 closes the connection after each answer, which is what ends a stream without a length.
    protocol_version = "HTTP/1.0"
    server: _Httpd

    def do_GET(self) -> None:
        self.server.fake.dispatch(self)

    def do_POST(self) -> None:
        self.server.fake.dispatch(self)

    def log_message(self, format: str, *args: object) -> None:
        """Keeps the test output quiet. The recorded requests are the log."""


def _send(handler: BaseHTTPRequestHandler, reply: Reply, stop_after: int | None = None) -> None:
    """Writes `reply`. With `stop_after`, sends only that many chunks (a cut-short stream)."""
    handler.send_response(reply.status)
    handler.send_header("Content-Type", reply.content_type)
    chunks = reply.chunks
    if not reply.stream:
        chunks = list(chunks)
        handler.send_header("Content-Length", str(sum(len(chunk) for chunk in chunks)))
    handler.end_headers()
    for index, chunk in enumerate(chunks):
        if stop_after is not None and index >= stop_after:
            break
        handler.wfile.write(chunk)
        handler.wfile.flush()
        if reply.chunk_delay_s:
            time.sleep(reply.chunk_delay_s)


def _drop(handler: BaseHTTPRequestHandler) -> None:
    """Closes the connection at once, so the client sees it end without a complete answer."""
    handler.close_connection = True
    # The client may already have left.
    with suppress(OSError):
        handler.connection.shutdown(socket.SHUT_RDWR)


def serve_forever(fake_type: type[ScriptedServer], default_port: int) -> None:
    """Entry point of a fake container: serves until the process ends, and exits with CRASH_EXIT_CODE on a crash.

    FAKE_PORT overrides the port, and FAKE_SCRIPT holds a JSON script (see ScriptedServer.load_script).
    """
    fake = fake_type(host=_ALL_INTERFACES, port=int(os.environ.get("FAKE_PORT", default_port)), on_crash=_exit_crashed)
    script = os.environ.get("FAKE_SCRIPT")
    if script:
        fake.load_script(script)
    fake.start()
    threading.Event().wait()


def _exit_crashed() -> None:
    os._exit(CRASH_EXIT_CODE)


def wait_for(condition: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    """Polls `condition` until it holds or `timeout_s` passes. Returns whether it held. For tests of work in flight."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def refuses_connections(port: int) -> bool:
    """Returns True when nothing accepts a connection on `port` of the local host, as after a crash."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except OSError:
        return True
    return False
