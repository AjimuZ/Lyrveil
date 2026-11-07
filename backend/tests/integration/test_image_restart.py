"""T-IMG-12 on a migrated database: a backend restart in each stage of an `image_request` resumes without duplicates
(docs/image-generation.md, Job stages and Persistence).

The first backend process runs the stages before the crash point normally, then dies in the middle of one: `Crash` is
a BaseException, so no handler or scheduler records anything, as when the process ends. The second process runs the
startup steps of docs/backend.md (staging cleanup, interrupted replies, interrupted jobs) and the remaining stages,
with the real scheduler and ImageGenerationService over a fake text runtime and a fake ComfyUI.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from integration.test_image_caption import ImageRuntime as FakeText
from integration.test_image_job import CAPTION, REQUEST, Events, FakeComfy, conversation_of, files, scalar
from integration.test_reply_streaming import Leases
from lyrveil.domain.messages import MessageStatus
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, recover_interrupted_jobs
from lyrveil.media.store import STAGING, MediaStore
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.image_runtime import ImageEvent, ImageProgress, ImageRequest
from lyrveil.ports.text_runtime import TextRequest
from lyrveil.services.chat_inference import recover_interrupted_replies
from lyrveil.services.image_generation import ImageGenerationService
from lyrveil.services.images import ImageService
from lyrveil.services.messages import MessageService

# The stages of an image request and the GPU mode each runs in.
STAGES = (("prompt", RequiredMode.LLM), ("render", RequiredMode.IMAGE), ("persist", RequiredMode.NONE))


class Crash(BaseException):
    """The backend process ends here: nothing after it runs, and no `except Exception` sees it."""


class CrashingText(FakeText):
    """The fake text runtime; with `crash`, the scene generation of the next `prompt` run ends the process."""

    def __init__(self) -> None:
        super().__init__(CAPTION)
        self.crash = False

    async def generate(self, request: TextRequest) -> str:
        if self.crash:
            self.crash = False
            raise Crash
        return await super().generate(request)


class CrashingComfy(FakeComfy):
    """The fake ComfyUI; with `crash`, the next render ends the process after its first progress message."""

    def __init__(self) -> None:
        super().__init__()
        self.crash = False

    async def render(self, request: ImageRequest) -> AsyncIterator[ImageEvent]:
        if self.crash:
            self.crash = False
            self.workflows.append(request.workflow)
            yield ImageProgress("loading")
            raise Crash
        async for event in super().render(request):
            yield event


class CrashingMedia(MediaStore):
    """The media store; `crash` names where the next `persist` run ends the process: `original`, after the thumbnail
    was written and before the original, so before the commit, or `discard`, after the commit, as the staged file is
    removed."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.crash: str | None = None

    def save(self, relative: str, data: bytes) -> None:
        if self.crash == "original" and relative.startswith("images/"):
            self.crash = None
            raise Crash
        super().save(relative, data)

    def discard_staged(self, job_id: str) -> None:
        if self.crash == "discard":
            self.crash = None
            raise Crash
        super().discard_staged(job_id)


class Backend:
    """One backend process over the shared database and media folder."""

    def __init__(self, engine: Engine, root: Path) -> None:
        self.engine = engine
        self.media = CrashingMedia(root)
        self.text = CrashingText()
        self.comfy = CrashingComfy()
        self.events = Events()
        self.queue = JobQueue()
        self.mode = RequiredMode.LLM
        service = ImageGenerationService(
            engine, self.text, self.comfy, self.media, self.events, seed_source=lambda: 111
        )
        self.scheduler = JobScheduler(engine, self.queue, {JobKind.IMAGE_REQUEST: service}, Leases(), lambda: self.mode)
        self.images = ImageService(engine, self.media, self.queue, seed_source=lambda: 111)

    def start(self) -> None:
        """Startup steps 4 and 5 of docs/backend.md, in the order main runs them."""
        self.media.clear_staging()
        recover_interrupted_replies(self.engine, self.queue)
        recover_interrupted_jobs(self.engine, self.queue)

    async def run(self, mode: RequiredMode) -> Job:
        self.mode = mode
        job = await self.scheduler.run_once()
        assert job is not None
        return job

    async def crash(self, mode: RequiredMode) -> None:
        """Runs the next stage, which must end the process."""
        self.mode = mode
        crashed = False
        try:
            await self.scheduler.run_once()
        except* Crash:
            crashed = True
        assert crashed, "the run did not reach the crash point"

    def job(self, job_id: str) -> Job:
        found = read(self.engine, lambda connection: self.queue.get(connection, job_id))
        assert found is not None
        return found

    def sent(self, event_type: str) -> int:
        return self.events.types().count(event_type)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stage", "point"),
    [
        pytest.param("prompt", "text", id="prompt"),
        pytest.param("render", "comfy", id="render"),
        pytest.param("persist", "original", id="persist before the commit"),
        pytest.param("persist", "discard", id="persist after the commit"),
    ],
)
async def test_a_restart_in_each_stage_resumes_without_duplicates(
    engine: Engine, tmp_path: Path, stage: str, point: str
) -> None:
    upgrade_to_head(engine)
    root = tmp_path / "data" / "media"
    before = Backend(engine, root)
    conversation_id = conversation_of(engine)
    sent = before.images.request(conversation_id, REQUEST)
    index = [name for name, _ in STAGES].index(stage)
    for _, mode in STAGES[:index]:
        await before.run(mode)
    if point == "text":
        before.text.crash = True
    elif point == "comfy":
        before.comfy.crash = True
    else:
        before.media.crash = point
    await before.crash(STAGES[index][1])
    cut = before.job(sent.job.id)
    assert (cut.status, cut.stage) == (JobStatus.RUNNING, stage)

    after = Backend(engine, root)
    after.start()
    recovered = after.job(sent.job.id)
    assert (recovered.status, recovered.stage, recovered.error_code) == (JobStatus.QUEUED, stage, "JOB_INTERRUPTED")
    for _, mode in STAGES[index:]:
        await after.run(mode)

    done = after.job(sent.job.id)
    assert (done.status, done.stage) == (JobStatus.COMPLETED, "persist")
    assert done.result is not None
    messages = MessageService(engine).history(conversation_id).items
    assert [(m.author.value, m.status) for m in messages] == [
        ("user", MessageStatus.COMPLETE),
        ("character", MessageStatus.COMPLETE),
    ]
    reply = messages[1]
    assert (reply.id, reply.content, reply.image_id) == (sent.reply.id, CAPTION, done.result["image_id"])
    assert reply.image_id is not None
    assert scalar(engine, "SELECT count(*) FROM generated_images") == 1
    assert scalar(engine, "SELECT count(*) FROM message_variants WHERE image_id IS NOT NULL") == 1
    assert scalar(engine, "SELECT count(*) FROM generation_jobs") == 1
    assert [path.suffix for path in files(root, "images")] == [".png"]
    assert [path.suffix for path in files(root, "thumbs")] == [".webp"]
    assert files(root, STAGING) == []
    metadata = after.images.get(reply.image_id)
    assert (metadata["quality_preset"], metadata["seed"]) == ("alta", 111)
    # Only the process that finished the job announces it, once.
    assert (after.sent("image.completed"), after.sent("message.completed")) == (1, 1)
    assert after.sent("message.failed") == before.sent("message.failed") == 0
