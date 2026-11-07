"""Checks reply streaming on a real database: T-MSG-03, T-MSG-09, T-MSG-12, T-MSG-13, T-SEC-06 (integration level).

The text runtime is a fake that keeps the TextRuntime contract; the job worker, the event hub, the WebSocket
route and the database are the real ones. The WebSocket is spoken in raw ASGI messages on the test's own event
loop, the loop of the job worker, so every wait has a deadline instead of blocking a test thread.
"""

import io
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import Connection, Engine, text
from starlette.types import Message as AsgiMessage

import lyrveil.main as backend
from lyrveil.domain.identifiers import new_id
from lyrveil.events.hub import EventHub
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, write
from lyrveil.logs import configure_logging
from lyrveil.main import Worker, create_app
from lyrveil.orchestration.client import Lease, LeaseMode, LeaseOutcome
from lyrveil.persistence.database import create_database_engine
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import RuntimeUnavailableError, TextChunk, TextRequest, TextRuntime, TextRuntimeInfo
from lyrveil.services.chat_inference import ChatInferenceService
from lyrveil.services.conversations import ConversationService
from lyrveil.services.messages import MessageService
from lyrveil.settings import Settings

NOW = "2026-11-07T10:00:00.000Z"
APP_URL = "http://localhost:4200"
API = "/api/v1"
TOKEN = "ab" * 32
MARKER = "MARKER-7f3a9c"
DEADLINE_S = 10.0
CURRENT_MESSAGE = "[Messaggio di Utente]\n"


def current_message(prompt: str) -> str:
    """Returns the user message a `gemma4` reply prompt ends with: the text after its last message marker."""
    return prompt.rsplit(CURRENT_MESSAGE, 1)[1].split("<turn|>", 1)[0]


class FakeRuntime:
    """A TextRuntime that answers `answer(current message)`, one word per token, and records each request.

    `after_token(index)` runs when the handler asks for the token after `index`. `failures` are raised, one per
    stream, after its first token. `hold_after` makes a stream stop after that many tokens and wait forever, calling
    `on_hold` first, as a generation cut by a crash does.
    """

    def __init__(self, answer: Callable[[str], str] = lambda current: f"Risposta a {current}") -> None:
        self.answer = answer
        self.requests: list[TextRequest] = []
        self.failures: list[Exception] = []
        self.after_token: Callable[[int], None] | None = None
        self.hold_after: int | None = None
        self.on_hold: Callable[[], None] | None = None

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        self.requests.append(request)
        words = self.answer(current_message(request.prompt)).split(" ")
        tokens = [word if index == 0 else f" {word}" for index, word in enumerate(words)]
        for index, token in enumerate(tokens):
            if index == 1 and self.failures:
                raise self.failures.pop(0)
            yield TextChunk(token)
            if self.after_token is not None:
                self.after_token(index)
            if self.hold_after == index + 1:
                if self.on_hold is not None:
                    self.on_hold()
                await anyio.sleep_forever()
        yield TextChunk("", "stop", "".join(tokens))

    async def generate(self, request: TextRequest) -> str:
        return self.answer(current_message(request.prompt))

    async def abort(self, genkey: str) -> bool:
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=8192)


@asynccontextmanager
async def opened(runtime: FakeRuntime) -> AsyncIterator[TextRuntime]:
    yield runtime


class Leases:
    """Grants every lease, as an orchestrator in LLM_READY with no other job would."""

    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease:
        return Lease(id=f"lease-{job_id}", mode=mode, job_id=job_id, ttl_s=30, expires_in_s=30.0)

    def renew_lease(self, lease: Lease) -> Lease:
        return lease

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        return True


class Ticks:
    """A monotonic clock that moves only when the test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Clock:
    """An aware clock for the job queue, so a retry backoff can pass at once."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Socket:
    """One connection to the app's /ws, spoken in raw ASGI messages; every wait fails after DEADLINE_S."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._to_app, self._app_inbox = anyio.create_memory_object_stream[AsgiMessage](100)
        self._app_outbox, self._from_app = anyio.create_memory_object_stream[AsgiMessage](100)

    async def run(self) -> None:
        scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "server": ("localhost", 4200),
            "client": ("127.0.0.1", 50000),
            "root_path": "",
            "path": "/ws",
            "raw_path": b"/ws",
            "query_string": b"",
            "headers": [(b"host", b"localhost:4200"), (b"origin", APP_URL.encode())],
            "subprotocols": [],
            "extensions": {"websocket.http.response": {}},
        }
        await self._app(scope, self._app_inbox.receive, self._app_outbox.send)

    async def connect(self) -> dict[str, Any]:
        """Opens the connection and returns the first frame, `connected`."""
        await self._to_app.send({"type": "websocket.connect"})
        accepted = await self._next()
        assert accepted["type"] == "websocket.accept", accepted
        return await self.receive_json()

    async def send_json(self, value: object) -> None:
        await self._to_app.send({"type": "websocket.receive", "text": json.dumps(value)})

    async def receive_json(self) -> dict[str, Any]:
        message = await self._next()
        assert message["type"] == "websocket.send", message
        frame: dict[str, Any] = json.loads(message["text"])
        return frame

    async def receive_until(self, *types: str) -> list[dict[str, Any]]:
        """Returns the frames up to and including the first one of `types`."""
        frames = [await self.receive_json()]
        while frames[-1]["type"] not in types:
            frames.append(await self.receive_json())
        return frames

    async def close(self) -> None:
        await self._to_app.send({"type": "websocket.disconnect", "code": 1000})

    async def _next(self) -> AsgiMessage:
        with anyio.fail_after(DEADLINE_S):
            return await self._from_app.receive()


@asynccontextmanager
async def connected(app: FastAPI) -> AsyncIterator[tuple[Socket, dict[str, Any]]]:
    """Yields an open connection and its `connected` frame, and closes it afterwards."""
    socket = Socket(app)
    async with anyio.create_task_group() as group:
        group.start_soon(socket.run)
        frame = await socket.connect()
        yield socket, frame
        await socket.close()


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Runs the app's lifespan, so its job worker, and yields a REST client on the same event loop."""
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url=APP_URL) as client:
        yield client


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    # Some tests point the root log handler at a buffer or at the captured stdout; later tests must not inherit it.
    yield
    configure_logging("INFO")


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


def new_chat(engine: Engine, first_name: str = "Alba") -> str:
    character_id = new_id()

    def work(connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, status, created_at, updated_at) "
                "VALUES (:id, :name, 30, 'active', :now, :now)"
            ),
            {"id": character_id, "name": first_name, "now": NOW},
        )

    write(engine, work)
    return ConversationService(engine).create(character_id).id


def stored(engine: Engine, message_id: str) -> tuple[str, str, str, str]:
    """Returns the status and text of the message, then those of its active variant."""
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT m.status, m.content, v.status, v.content FROM messages AS m "
                "JOIN message_variants AS v ON v.id = m.active_variant_id WHERE m.id = :id"
            ),
            {"id": message_id},
        ).one()
    return row[0], row[1], row[2], row[3]


def job_of(engine: Engine, message_id: str) -> Job:
    with engine.connect() as connection:
        job_id = connection.execute(
            text("SELECT id FROM generation_jobs WHERE message_id = :id"), {"id": message_id}
        ).scalar_one()
    job = read(engine, lambda connection: JobQueue().get(connection, job_id))
    assert job is not None
    return job


def worker_for(
    engine: Engine,
    runtime: FakeRuntime,
    hub: EventHub,
    ticks: Ticks | None = None,
    queue: JobQueue | None = None,
    flush_interval_s: float = 2.0,
) -> tuple[ChatInferenceService, JobScheduler]:
    service = ChatInferenceService(engine, runtime, hub, flush_interval_s=flush_interval_s, monotonic=ticks or Ticks())
    handlers = {JobKind.CHAT_REPLY: service, JobKind.CHAT_REGENERATE: service}
    return service, JobScheduler(engine, queue or JobQueue(), handlers, Leases(), lambda: RequiredMode.LLM)


def events(hub: EventHub) -> list[tuple[str, dict[str, Any]]]:
    published = hub.missed(0, hub.boot_id)
    assert published is not None
    return [(event.type, dict(event.data)) for event in published]


def deltas_of(published: list[tuple[str, dict[str, Any]]], message_id: str) -> list[dict[str, Any]]:
    return [data for kind, data in published if kind == "message.delta" and data["message_id"] == message_id]


@pytest.mark.anyio
async def test_a_reply_streams_its_tokens_in_order_flushes_every_two_seconds_and_stores_the_whole_text(
    migrated: Engine,
) -> None:
    reply = MessageService(migrated).send(new_chat(migrated), "Ciao").reply
    ticks = Ticks()
    runtime = FakeRuntime(lambda _current: "t0 t1 t2 t3 t4 t5 t6 t7")
    seen: list[tuple[str, str, str, str]] = []

    def after_token(_index: int) -> None:
        # The next token takes 0.9 s; meanwhile the database holds what the last flush wrote.
        ticks.now += 0.9
        seen.append(stored(migrated, reply.id))

    runtime.after_token = after_token
    hub = EventHub("boot")
    _, scheduler = worker_for(migrated, runtime, hub, ticks)
    finished = await scheduler.run_once()
    assert finished is not None
    assert finished.status is JobStatus.COMPLETED

    # Flushes at 2.7 s and 5.4 s, the first moments 2 s or more after the previous write.
    first, second, whole = "t0 t1 t2 t3", "t0 t1 t2 t3 t4 t5 t6", "t0 t1 t2 t3 t4 t5 t6 t7"
    assert [message_text for _, message_text, _, _ in seen] == ["", "", "", first, first, first, second, second]
    assert {(row[0], row[2]) for row in seen} == {("streaming", "streaming")}
    assert all(row[1] == row[3] for row in seen)
    assert stored(migrated, reply.id) == ("complete", whole, "complete", whole)

    published = events(hub)
    assert [kind for kind, _ in published] == [
        "typing.started",
        *["message.delta"] * 8,
        "typing.stopped",
        "message.completed",
    ]
    deltas = deltas_of(published, reply.id)
    assert "".join(delta["text"] for delta in deltas) == whole
    assert [delta["offset"] for delta in deltas] == [0, 2, 5, 8, 11, 14, 17, 20]
    assert {delta["variant_id"] for delta in deltas} == {reply.active_variant_id}
    completed = published[-1][1]["message"]
    assert (completed["id"], completed["status"], completed["content"]) == (reply.id, "complete", whole)

    (request,) = runtime.requests
    assert request.prompt.endswith(f"{CURRENT_MESSAGE}Ciao<turn|>\n<|turn>model\n<|channel>thought\n<channel|>")
    assert request.stop_sequence == ("<turn|>", "<|turn>", "\nUtente:")
    assert (request.max_context_length, request.max_length) == (8192, 400)
    assert request.genkey == finished.genkey


@pytest.mark.anyio
async def test_simultaneous_chats_are_answered_one_at_a_time_in_order_with_no_mixed_text(migrated: Engine) -> None:
    harbour, lighthouse = new_chat(migrated, "Alba"), new_chat(migrated, "Bruno")
    service = MessageService(migrated)
    first = service.send(harbour, "la mappa del porto").reply
    second = service.send(lighthouse, "il faro di notte").reply
    runtime = FakeRuntime()
    hub = EventHub("boot")
    _, scheduler = worker_for(migrated, runtime, hub)
    assert await scheduler.run_once() is not None
    assert await scheduler.run_once() is not None

    assert stored(migrated, first.id)[1] == "Risposta a la mappa del porto"
    assert stored(migrated, second.id)[1] == "Risposta a il faro di notte"
    published = events(hub)
    chat_of = {first.id: harbour, second.id: lighthouse}

    def chat(kind: str, data: dict[str, Any]) -> str:
        if kind.startswith("typing."):
            return str(data["conversation_id"])
        if kind == "message.delta":
            return chat_of[data["message_id"]]
        return str(data["message"]["conversation_id"])

    owners = [chat(kind, data) for kind, data in published]
    assert owners == [harbour] * owners.count(harbour) + [lighthouse] * owners.count(lighthouse)
    for reply in (first, second):
        deltas = deltas_of(published, reply.id)
        assert "".join(delta["text"] for delta in deltas) == stored(migrated, reply.id)[1]
    harbour_prompt, lighthouse_prompt = (request.prompt for request in runtime.requests)
    assert "Alba" in harbour_prompt
    assert "Bruno" in lighthouse_prompt
    assert "faro" not in harbour_prompt
    assert "porto" not in lighthouse_prompt
    assert "Alba" not in lighthouse_prompt


@pytest.mark.anyio
async def test_a_runtime_failure_requeues_the_reply_empty_and_the_retry_overwrites_the_same_variant(
    migrated: Engine,
) -> None:
    clock = Clock()
    queue = JobQueue(clock=clock)
    reply = MessageService(migrated, queue).send(new_chat(migrated), "Ciao").reply
    runtime = FakeRuntime(lambda _current: "Seconda prova riuscita")
    runtime.failures.append(RuntimeUnavailableError("the stream broke"))
    hub = EventHub("boot")
    _, scheduler = worker_for(migrated, runtime, hub, queue=queue)

    retried = await scheduler.run_once()
    assert retried is not None
    assert (retried.status, retried.error_code) == (JobStatus.QUEUED, "RUNTIME_UNAVAILABLE")
    assert stored(migrated, reply.id) == ("queued", "", "streaming", "")
    published = events(hub)
    assert [kind for kind, _ in published] == ["typing.started", "message.delta", "typing.stopped", "message.updated"]
    assert published[-1][1]["message"]["status"] == "queued"

    assert await scheduler.run_once() is None, "the retry must wait for its backoff"
    clock.advance(5)
    done = await scheduler.run_once()
    assert done is not None
    assert done.status is JobStatus.COMPLETED
    assert stored(migrated, reply.id) == ("complete", "Seconda prova riuscita", "complete", "Seconda prova riuscita")
    variants = MessageService(migrated).variants(reply.id)
    assert [variant.id for variant in variants] == [reply.active_variant_id]


def start(monkeypatch: pytest.MonkeyPatch, database_path: Path) -> list[FastAPI]:
    """Prepares main() with a stand-in for Uvicorn and returns the applications it is asked to serve."""
    served: list[FastAPI] = []

    def fake_run(app: FastAPI, **_options: Any) -> None:
        served.append(app)

    monkeypatch.setenv("LYRVEIL_CONTROL_TOKEN", TOKEN)
    monkeypatch.setattr(backend, "DATABASE_PATH", database_path)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    return served


def test_a_reply_cut_while_streaming_keeps_its_text_and_is_interrupted_at_the_next_start(
    monkeypatch: pytest.MonkeyPatch, database_path: Path
) -> None:
    engine = create_database_engine(database_path)
    upgrade_to_head(engine)
    reply = MessageService(engine).send(new_chat(engine), "Ciao").reply
    runtime = FakeRuntime(lambda _current: "uno due tre quattro cinque")
    runtime.hold_after = 3

    async def cut() -> None:
        held = anyio.Event()
        runtime.on_hold = held.set
        service, scheduler = worker_for(engine, runtime, EventHub("boot"), flush_interval_s=0.0)
        async with anyio.create_task_group() as group:
            group.start_soon(scheduler.run_once)
            with anyio.fail_after(DEADLINE_S):
                await held.wait()
            # A shutdown, or a crash after the last flush: the worker stops in the middle of the stream.
            service.close()
            group.cancel_scope.cancel()

    anyio.run(cut)
    assert stored(engine, reply.id) == ("streaming", "uno due tre", "streaming", "uno due tre")
    assert job_of(engine, reply.id).status is JobStatus.RUNNING
    engine.dispose()

    served = start(monkeypatch, database_path)
    assert backend.main() == 0
    (app,) = served
    restarted: Engine = app.state.engine
    assert stored(restarted, reply.id) == ("interrupted", "uno due tre", "interrupted", "uno due tre")
    job = job_of(restarted, reply.id)
    assert (job.status, job.error_code) == (JobStatus.FAILED, "JOB_INTERRUPTED")
    restarted.dispose()


@pytest.mark.anyio
async def test_a_reply_reaches_the_socket_in_order_and_a_reconnect_gets_the_replay_or_a_resync(
    settings: Settings, migrated: Engine
) -> None:
    chat = new_chat(migrated)
    runtime = FakeRuntime(lambda _current: "Ciao, com'è andata?")
    app = create_app(settings, migrated, worker=Worker(text_runtime=lambda: opened(runtime), leases=Leases()))
    # A small buffer, so the six events of one reply already push the oldest out.
    app.state.events = EventHub(app.state.boot_id, capacity=4)
    with anyio.fail_after(DEADLINE_S):
        async with running(app) as client:
            async with connected(app) as (socket, hello):
                assert hello == {"v": 1, "type": "connected", "boot_id": app.state.boot_id}
                await socket.send_json({"type": "hello", "last_event_id": None, "boot_id": None})
                sent = await client.post(f"{API}/conversations/{chat}/messages", json={"content": "Ciao"})
                assert sent.status_code == 202, sent.text
                frames = await socket.receive_until("message.completed")

            assert [frame["type"] for frame in frames] == [
                "typing.started",
                "message.delta",
                "message.delta",
                "message.delta",
                "typing.stopped",
                "message.completed",
            ]
            assert [frame["id"] for frame in frames] == [1, 2, 3, 4, 5, 6]
            reply = frames[-1]["data"]["message"]
            assert (reply["id"], reply["status"]) == (sent.json()["reply"]["id"], "complete")
            streamed = "".join(frame["data"]["text"] for frame in frames[1:4])
            assert streamed == reply["content"] == "Ciao, com'è andata?"

            async with connected(app) as (socket, _):
                await socket.send_json({"type": "hello", "last_event_id": 2, "boot_id": hello["boot_id"]})
                replay = [await socket.receive_json() for _ in frames[2:]]
            assert replay == frames[2:]

            # Event 2 is no longer buffered, and another boot_id names another process: both get a resync at 6.
            for position, boot_id in ((1, hello["boot_id"]), (6, "another-boot")):
                async with connected(app) as (socket, _):
                    await socket.send_json({"type": "hello", "last_event_id": position, "boot_id": boot_id})
                    resync = await socket.receive_json()
                assert (resync["type"], resync["id"], resync["data"]) == ("resync.required", 6, {})

            page = (await client.get(f"{API}/conversations/{chat}/messages")).json()
            assert [(item["status"], item["content"]) for item in page["items"]] == [
                ("complete", "Ciao"),
                ("complete", "Ciao, com'è andata?"),
            ]


@pytest.mark.anyio
async def test_marker_strings_never_reach_the_logs(settings: Settings, migrated: Engine) -> None:
    buffer = io.StringIO()
    configure_logging("DEBUG", buffer)
    chat = new_chat(migrated)

    def answer(current: str) -> str:
        if current.startswith("guasto"):
            raise RuntimeError(f"guasto {MARKER}")
        return f"Ecco {MARKER} per te"

    runtime = FakeRuntime(answer)
    app = create_app(settings, migrated, worker=Worker(text_runtime=lambda: opened(runtime), leases=Leases()))
    endings: list[dict[str, Any]] = []
    with anyio.fail_after(DEADLINE_S):
        async with running(app) as client, connected(app) as (socket, _):
            await socket.send_json({"type": "hello", "last_event_id": None, "boot_id": None})
            for number, content in enumerate((f"Ciao {MARKER}", f"guasto {MARKER}")):
                body = {"content": content, "client_message_id": f"{MARKER}-{number}"}
                response = await client.post(f"{API}/conversations/{chat}/messages", json=body)
                assert response.status_code == 202, response.text
                endings.append((await socket.receive_until("message.completed", "message.failed"))[-1])
    assert [frame["type"] for frame in endings] == ["message.completed", "message.failed"]
    assert endings[1]["data"]["error_code"] == "INTERNAL"
    output = buffer.getvalue()
    for event in ("job_started", "reply_finished", "reply_failed", "job_handler_failed"):
        assert event in output, f"{event} was not logged, so its path was not checked"
    assert MARKER not in output
