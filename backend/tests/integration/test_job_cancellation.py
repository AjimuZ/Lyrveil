"""P06.3 on a migrated database: T-IMG-10, the cancellation of an image at each stage, a running reply that keeps its
text, the `POST /jobs/{id}/cancel` route, and the scheduler side of T-ORC-12, the preemption of background work.

The image runs its real stages with the fake text runtime of test_image_caption.py and a fake ComfyUI, under granted
leases; the GPU mode is set by the test as in test_image_job.py. A running stage is held inside its fake runtime, so
the cancellation always arrives while it runs (docs/gpu-orchestration.md, Cancellation).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from integration.test_image_caption import ImageRuntime as FakeText
from integration.test_image_job import CAPTION, REQUEST, Events, Harness, conversation_of, scalar
from integration.test_image_mode_switch import HeldComfy
from integration.test_reply_streaming import FakeRuntime, Leases, new_chat, stored
from lyrveil.domain.errors import ConflictError, NotFoundError
from lyrveil.domain.messages import MessageStatus
from lyrveil.jobs.handlers import JobDone, JobHandler, JobOutcome, RunContext, StopReason
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import JOB_CANCELLED, Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, write
from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import TextChunk, TextRequest
from lyrveil.services.chat_inference import ChatInferenceService
from lyrveil.services.image_generation import ImageGenerationService
from lyrveil.services.job_control import JobControlService
from lyrveil.services.messages import MessageService
from lyrveil.settings import Settings

API = "/api/v1"


class HeldText(FakeText):
    """The fake text runtime of the image prompt, whose caption stream sends one word, sets `streaming` and then
    waits until the test sets `release`."""

    def __init__(self) -> None:
        super().__init__(CAPTION)
        self.streaming = anyio.Event()
        self.release = anyio.Event()

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        self.calls.append(("stream", request))
        yield TextChunk("Eccola,")
        self.streaming.set()
        await self.release.wait()
        yield TextChunk("", "stop", "Eccola,")


class StoppableComfy(HeldComfy):
    """A HeldComfy that counts the interrupts it receives."""

    def __init__(self) -> None:
        super().__init__()
        self.interrupts = 0

    async def interrupt(self) -> bool:
        self.interrupts += 1
        return True


class Mode:
    """The GPU mode whose stages may run, set by the test."""

    def __init__(self) -> None:
        self.mode = RequiredMode.LLM

    def __call__(self) -> RequiredMode:
        return self.mode


class QueueClock:
    """The aware clock of the job queue, moved only by the test, so a job queued later is visibly newer."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def harness(engine: Engine, tmp_path: Path) -> Harness:
    upgrade_to_head(engine)
    return Harness(engine, MediaStore(tmp_path / "media"), [111, 222, 333])


def image_worker(
    harness: Harness, text: FakeText, comfy: HeldComfy, mode: Mode
) -> tuple[ImageGenerationService, JobScheduler]:
    service = ImageGenerationService(harness.engine, text, comfy, harness.media, harness.events, seed_source=lambda: 7)
    worker = JobScheduler(
        harness.engine,
        harness.queue,
        {JobKind.IMAGE_REQUEST: service},
        Leases(),
        mode,
        text_runtime=text,
        image_runtime=comfy,
    )
    return service, worker


async def run_and_cancel(worker: JobScheduler, running: anyio.Event, cancel: JobControlService, job_id: str) -> Job:
    """Starts one pass, cancels `job_id` once `running` is set, wakes the worker as the route does, and returns the
    job the pass ended."""
    ended: list[Job | None] = []

    async def run() -> None:
        ended.append(await worker.run_once())

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(run)
            await running.wait()
            requested = cancel.cancel(job_id)
            assert (requested.job.status, requested.events) == (JobStatus.RUNNING, ())
            assert requested.job.cancel_requested_at is not None
            worker.notify()
    [job] = ended
    assert job is not None
    return job


def visible(engine: Engine, conversation_id: str) -> dict[str, MessageStatus]:
    return {message.id: message.status for message in MessageService(engine).history(conversation_id).items}


@pytest.mark.anyio
async def test_a_queued_image_is_cancelled_at_once_in_each_stage_and_its_placeholder_removed(
    harness: Harness,
) -> None:
    conversation_id = conversation_of(harness.engine)
    cancel = JobControlService(harness.engine, harness.media)

    # Stage `prompt`, before anything ran.
    first = harness.images.request(conversation_id, REQUEST)
    cancelled = cancel.cancel(first.job.id)
    assert (cancelled.job.status, cancelled.job.error_code) == (JobStatus.CANCELLED, JOB_CANCELLED)
    assert cancelled.events == (("message.deleted", {"message_id": first.reply.id}),)
    shown = visible(harness.engine, conversation_id)
    assert first.reply.id not in shown
    assert shown[first.message.id] is MessageStatus.COMPLETE  # The user's request stays.
    assert await harness.scheduler.run_once() is None

    # Stage `render`, after the prompt ran.
    second = harness.images.request(conversation_id, REQUEST)
    await harness.run_stage(RequiredMode.LLM)
    assert harness.job(second.job.id).stage == "render"
    assert cancel.cancel(second.job.id).job.status is JobStatus.CANCELLED
    assert second.reply.id not in visible(harness.engine, conversation_id)
    harness.mode = RequiredMode.IMAGE
    assert await harness.scheduler.run_once() is None
    assert harness.comfy.workflows == []

    # Stage `persist`, with the rendered file staged: the file goes too, and no image is stored.
    third = harness.images.request(conversation_id, REQUEST)
    await harness.run_stage(RequiredMode.LLM)
    await harness.run_stage(RequiredMode.IMAGE)
    assert harness.job(third.job.id).stage == "persist"
    assert harness.media.read_staged(third.job.id) is not None
    assert cancel.cancel(third.job.id).job.status is JobStatus.CANCELLED
    assert harness.media.read_staged(third.job.id) is None
    assert third.reply.id not in visible(harness.engine, conversation_id)
    harness.mode = RequiredMode.NONE
    assert await harness.scheduler.run_once() is None
    assert scalar(harness.engine, "SELECT count(*) FROM generated_images") == 0

    # An ended job, and an unknown one, cannot be cancelled.
    with pytest.raises(ConflictError):
        cancel.cancel(third.job.id)
    with pytest.raises(NotFoundError):
        cancel.cancel("missing")


@pytest.mark.anyio
async def test_a_running_prompt_is_stopped_and_its_generation_aborted(harness: Harness) -> None:
    text, comfy, mode = HeldText(), StoppableComfy(), Mode()
    _, worker = image_worker(harness, text, comfy, mode)
    conversation_id = conversation_of(harness.engine)
    sent = harness.images.request(conversation_id, REQUEST)

    ended = await run_and_cancel(worker, text.streaming, JobControlService(harness.engine, harness.media), sent.job.id)

    assert (ended.id, ended.status, ended.error_code, ended.stage) == (
        sent.job.id,
        JobStatus.CANCELLED,
        JOB_CANCELLED,
        "prompt",
    )
    # The scheduler aborted the generation of the run under its lease, and the handler then removed the placeholder.
    assert text.aborted == [ended.genkey]
    assert ("message.deleted", {"message_id": sent.reply.id}) in harness.events.sent
    assert sent.reply.id not in visible(harness.engine, conversation_id)
    assert comfy.workflows == []
    assert await worker.run_once() is None


@pytest.mark.anyio
async def test_a_running_render_is_interrupted_and_leaves_no_file_and_no_image(harness: Harness) -> None:
    text, comfy, mode = FakeText(CAPTION), StoppableComfy(), Mode()
    _, worker = image_worker(harness, text, comfy, mode)
    conversation_id = conversation_of(harness.engine)
    sent = harness.images.request(conversation_id, REQUEST)
    await worker.run_once()  # The prompt stage.
    mode.mode = RequiredMode.IMAGE

    ended = await run_and_cancel(worker, comfy.rendering, JobControlService(harness.engine, harness.media), sent.job.id)

    assert (ended.status, ended.stage, ended.error_code) == (JobStatus.CANCELLED, "render", JOB_CANCELLED)
    assert comfy.interrupts == 1
    assert len(comfy.workflows) == 1
    assert harness.media.read_staged(sent.job.id) is None
    assert sent.reply.id not in visible(harness.engine, conversation_id)
    assert ("message.deleted", {"message_id": sent.reply.id}) in harness.events.sent
    # The cancelled render recorded no failure on the message before it was removed.
    assert ("message.failed", {"message_id": sent.reply.id, "error_code": "RUNTIME_UNAVAILABLE"}) not in (
        harness.events.sent
    )
    mode.mode = RequiredMode.NONE
    assert await worker.run_once() is None
    assert scalar(harness.engine, "SELECT count(*) FROM generated_images") == 0


@pytest.mark.anyio
async def test_a_running_persist_is_not_cancelled(harness: Harness) -> None:
    conversation_id = conversation_of(harness.engine)
    sent = harness.images.request(conversation_id, REQUEST)
    await harness.run_stage(RequiredMode.LLM)
    await harness.run_stage(RequiredMode.IMAGE)
    write(harness.engine, lambda connection: harness.queue.start(connection, sent.job.id, None, "genkey"))

    with pytest.raises(ConflictError):
        JobControlService(harness.engine, harness.media).cancel(sent.job.id)
    assert harness.job(sent.job.id).cancel_requested_at is None


@pytest.mark.anyio
async def test_a_cancelled_running_reply_keeps_its_text_as_interrupted(engine: Engine) -> None:
    upgrade_to_head(engine)
    runtime = FakeRuntime(lambda _current: "uno due tre quattro cinque")
    streaming = anyio.Event()
    runtime.hold_after = 3
    runtime.on_hold = streaming.set
    events = Events()
    service = ChatInferenceService(engine, runtime, events)
    worker = JobScheduler(
        engine, JobQueue(), {JobKind.CHAT_REPLY: service}, Leases(), lambda: RequiredMode.LLM, text_runtime=runtime
    )
    conversation_id = new_chat(engine)
    sent = MessageService(engine).send(conversation_id, "Ciao", intent="text")

    ended = await run_and_cancel(worker, streaming, JobControlService(engine), sent.job.id)

    assert (ended.status, ended.error_code) == (JobStatus.CANCELLED, JOB_CANCELLED)
    assert runtime.aborted == [ended.genkey]
    # The text shown before the cancellation stays, and the reply reads as interrupted.
    assert stored(engine, sent.reply.id) == ("interrupted", "uno due tre", "interrupted", "uno due tre")
    kinds = [kind for kind, _ in events.sent]
    assert kinds[-2:] == ["typing.stopped", "message.completed"]
    assert "message.deleted" not in kinds


@pytest.mark.anyio
async def test_the_cancel_route_answers_the_job_and_refuses_what_cannot_be_cancelled(
    harness: Harness, settings: Settings
) -> None:
    conversation_id = conversation_of(harness.engine)
    sent = harness.images.request(conversation_id, REQUEST)
    client = TestClient(
        create_app(settings, harness.engine, media_store=harness.media), base_url="http://localhost:4200"
    )

    answer = client.post(f"{API}/jobs/{sent.job.id}/cancel")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["status"], body["error_code"], body["cancel_requested"], body["queue_position"]) == (
        "cancelled",
        "JOB_CANCELLED",
        True,
        0,
    )
    assert client.get(f"{API}/jobs/{sent.job.id}").json()["status"] == "cancelled"
    messages = client.get(f"{API}/conversations/{conversation_id}/messages").json()["items"]
    assert [item["id"] for item in messages] == [sent.message.id]

    again = client.post(f"{API}/jobs/{sent.job.id}/cancel")
    assert (again.status_code, again.json()["code"]) == (409, "CONFLICT")
    missing = client.post(f"{API}/jobs/missing/cancel")
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")


class Background:
    """A background handler that holds its generation until `release` is set, and records the genkey of each run and
    the stop reason of each cancelled one."""

    def __init__(self) -> None:
        self.started = anyio.Event()
        self.release = anyio.Event()
        self.genkeys: list[str] = []
        self.stops: list[StopReason | None] = []

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        self.genkeys.append(context.genkey)
        self.started.set()
        try:
            await self.release.wait()
        except anyio.get_cancelled_exc_class():
            self.stops.append(context.stop.reason)
            raise
        return JobDone()


class Done:
    """A handler that finishes every job at once and records the ids it ran."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        self.ran.append(job.id)
        return JobDone()


def enqueue(engine: Engine, queue: JobQueue, kind: JobKind) -> Job:
    return write(engine, lambda connection: queue.enqueue(connection, kind))


@pytest.mark.anyio
async def test_a_foreground_job_preempts_background_generation_at_no_attempt_cost(engine: Engine) -> None:
    upgrade_to_head(engine)
    clock = QueueClock()
    queue = JobQueue(clock=clock)
    runtime = FakeRuntime()
    background, replies = Background(), Done()
    handlers: dict[JobKind, JobHandler] = {JobKind.MEMORY_EXTRACTION: background, JobKind.CHAT_REPLY: replies}
    worker = JobScheduler(engine, queue, handlers, Leases(), lambda: RequiredMode.LLM, text_runtime=runtime)
    extraction = enqueue(engine, queue, JobKind.MEMORY_EXTRACTION)
    ended: list[Job | None] = []

    async def run() -> None:
        ended.append(await worker.run_once())

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(run)
            await background.started.wait()
            clock.advance(1.0)
            reply = enqueue(engine, queue, JobKind.CHAT_REPLY)
            worker.notify()

    [preempted] = ended
    assert preempted is not None
    assert (preempted.id, preempted.status, preempted.attempt, preempted.error_code) == (
        extraction.id,
        JobStatus.QUEUED,
        0,
        None,
    )
    assert background.stops == [StopReason.PREEMPTED]
    assert runtime.aborted == background.genkeys
    # The reply runs next, then the background job again, with its attempt given back.
    assert (await worker.run_once()) is not None
    assert replies.ran == [reply.id]
    background.release.set()
    again = await worker.run_once()
    assert again is not None
    assert (again.id, again.status, again.attempt) == (extraction.id, JobStatus.COMPLETED, 1)


@pytest.mark.anyio
async def test_foreground_work_is_never_preempted_and_old_foreground_work_preempts_nothing(engine: Engine) -> None:
    upgrade_to_head(engine)
    clock = QueueClock()
    queue = JobQueue(clock=clock)
    runtime = FakeRuntime()
    reply_handler, background = Background(), Background()
    handlers: dict[JobKind, JobHandler] = {
        JobKind.CHAT_REPLY: reply_handler,
        JobKind.MEMORY_EXTRACTION: background,
        JobKind.IMAGE_REQUEST: Done(),
    }
    worker = JobScheduler(engine, queue, handlers, Leases(), lambda: RequiredMode.LLM, text_runtime=runtime)

    # A running reply goes on when another reply arrives.
    enqueue(engine, queue, JobKind.CHAT_REPLY)
    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(worker.run_once)
            await reply_handler.started.wait()
            clock.advance(1.0)
            second = enqueue(engine, queue, JobKind.CHAT_REPLY)
            worker.notify()
            await anyio.sleep(0.05)
            reply_handler.release.set()
    assert reply_handler.stops == []
    after = await worker.run_once()
    assert after is not None
    assert (after.id, after.status) == (second.id, JobStatus.COMPLETED)

    # A foreground job queued before the background run started, here an image waiting for a mode this worker is not
    # in, does not stop it.
    write(engine, lambda connection: queue.enqueue(connection, JobKind.IMAGE_REQUEST, stage="render"))
    clock.advance(1.0)
    extraction = enqueue(engine, queue, JobKind.MEMORY_EXTRACTION)
    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(worker.run_once)
            await background.started.wait()
            worker.notify()
            await anyio.sleep(0.05)
            background.release.set()
    assert background.stops == []
    assert runtime.aborted == []
    done = read(engine, lambda connection: queue.get(connection, extraction.id))
    assert done is not None
    assert done.status is JobStatus.COMPLETED
