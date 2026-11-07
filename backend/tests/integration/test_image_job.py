"""T-IMG-06 to T-IMG-09 on a migrated database: the `image_request` job through the real scheduler, with a fake text
runtime and a fake ComfyUI. The stages are `prompt` (llm), `render` (image) and `persist` (none); the test moves the GPU
mode between the runs as the orchestrator would.
"""

import io
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Connection, Engine, text

from integration.test_image_caption import CARD
from integration.test_image_caption import ImageRuntime as FakeText
from integration.test_reply_streaming import Leases
from lyrveil.domain.errors import ValidationFailedError
from lyrveil.domain.identifiers import new_id
from lyrveil.domain.messages import MessageStatus
from lyrveil.jobs.handlers import JobFailedError, RunContext
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, write
from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.image_runtime import (
    ImageEvent,
    ImageOutput,
    ImageProgress,
    ImageRequest,
    ImageRuntimeInfo,
    ImageRuntimeUnavailableError,
    RenderFinished,
)
from lyrveil.services.conversations import ConversationService
from lyrveil.services.image_generation import ImageGenerationService
from lyrveil.services.images import ImageService
from lyrveil.services.messages import MessageService
from lyrveil.settings import Settings

STAMP = "2026-11-07T10:00:00.000Z"
REQUEST = "una foto di te seduta al porto"
CAPTION = "Eccola, il porto al tramonto."
SIZE = (128, 192)


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", SIZE, (30, 90, 160)).save(buffer, "PNG")
    return buffer.getvalue()


class FakeComfy:
    """An ImageRuntime that renders `png()` for every workflow. `workflows` records each one submitted; once `off` is
    set, every call fails as a stopped container would."""

    def __init__(self) -> None:
        self.workflows: list[dict[str, Any]] = []
        self.off = False

    async def render(self, request: ImageRequest) -> AsyncIterator[ImageEvent]:
        if self.off:
            raise ImageRuntimeUnavailableError("ComfyUI did not answer")
        self.workflows.append(request.workflow)
        yield ImageProgress("loading")
        yield ImageProgress("sampling", 14, 28)
        yield RenderFinished("success", (ImageOutput("lyrveil_00001_.png", "", "output"),))

    async def download(self, image: ImageOutput) -> bytes:
        if self.off:
            raise ImageRuntimeUnavailableError("ComfyUI did not answer")
        return png()

    async def interrupt(self) -> bool:
        return True

    async def info(self) -> ImageRuntimeInfo:
        if self.off:
            raise ImageRuntimeUnavailableError("ComfyUI did not answer")
        return ImageRuntimeInfo(version="0.3.77", devices=())


class Events:
    """An EventPublisher that records `(type, data)` in order."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Mapping[str, Any]]] = []

    def publish(self, event_type: str, data: Mapping[str, Any]) -> int:
        self.sent.append((event_type, data))
        return len(self.sent)

    def types(self) -> list[str]:
        return [kind for kind, _ in self.sent]


class Harness:
    """The service, the scheduler whose GPU mode the test sets, and the stores they share."""

    def __init__(self, engine: Engine, media: MediaStore, seeds: list[int]) -> None:
        self.engine = engine
        self.media = media
        self.text = FakeText(CAPTION)
        self.comfy = FakeComfy()
        self.events = Events()
        self.mode = RequiredMode.LLM
        self.queue = JobQueue()
        self.service = ImageGenerationService(
            engine,
            self.text,
            self.comfy,
            media,
            self.events,
            seed_source=lambda: seeds.pop(0),
        )
        self.scheduler = JobScheduler(
            engine, self.queue, {JobKind.IMAGE_REQUEST: self.service}, Leases(), lambda: self.mode
        )
        self.images = ImageService(engine, media, self.queue, seed_source=lambda: seeds.pop(0))

    async def run_stage(self, mode: RequiredMode) -> Job:
        self.mode = mode
        job = await self.scheduler.run_once()
        assert job is not None
        return job

    async def run_all(self) -> Job:
        """Runs `prompt`, `render` and `persist` in turn and returns the job after the last run."""
        await self.run_stage(RequiredMode.LLM)
        await self.run_stage(RequiredMode.IMAGE)
        return await self.run_stage(RequiredMode.NONE)

    def job(self, job_id: str) -> Job:
        found = read(self.engine, lambda connection: self.queue.get(connection, job_id))
        assert found is not None
        return found


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "media"


@pytest.fixture
def harness(engine: Engine, media_root: Path) -> Harness:
    upgrade_to_head(engine)
    return Harness(engine, MediaStore(media_root), [111, 222, 333])


def conversation_of(engine: Engine) -> str:
    character_id = new_id()
    write(engine, lambda c: character_repository.insert(c, character_id=character_id, card=CARD, now=STAMP))
    return ConversationService(engine).create(character_id).id


def scalar(engine: Engine, sql: str, **params: object) -> Any:
    def work(connection: Connection) -> Any:
        return connection.execute(text(sql), params).scalar_one()

    return read(engine, work)


def files(root: Path, folder: str) -> list[Path]:
    return sorted(path for path in (root / folder).rglob("*") if path.is_file())


@pytest.mark.anyio
async def test_the_image_appears_as_a_message_with_its_metadata(harness: Harness) -> None:
    conversation_id = conversation_of(harness.engine)
    sent = harness.images.request(conversation_id, REQUEST)
    assert (sent.reply.kind, sent.reply.status, sent.reply.content) == ("image", MessageStatus.QUEUED, "")
    assert sent.job.kind == "image_request"

    done = await harness.run_all()
    assert (done.status, done.stage) == (JobStatus.COMPLETED, "persist")

    messages = MessageService(harness.engine).history(conversation_id).items
    assert [(m.author.value, m.kind, m.status) for m in messages] == [
        ("user", "text", MessageStatus.COMPLETE),
        ("character", "mixed", MessageStatus.COMPLETE),
    ]
    reply = messages[1]
    assert reply.content == CAPTION
    assert reply.image_id is not None
    assert done.result == {"image_id": reply.image_id, "message_id": reply.id}

    metadata = harness.images.get(reply.image_id)
    assert (metadata["width"], metadata["height"], metadata["media_type"]) == (*SIZE, "image/png")
    assert (metadata["user_request"], metadata["purpose"], metadata["message_id"]) == (REQUEST, "chat", reply.id)
    # The framing of the example scene is "upper body", so `auto` resolves to portrait; Alta is the default preset.
    assert (metadata["aspect"], metadata["quality_preset"], metadata["seed"]) == ("portrait", "alta", 111)
    assert metadata["prompt_positive"].startswith("masterpiece, best quality")
    assert metadata["scene"]["framing"] == "upper body"
    assert (metadata["profile_id"], metadata["workflow_id"], metadata["comfyui_version"]) == (
        "illustrious-xl-v2.0",
        "sdxl_hires.v1.json",
        "0.3.77",
    )
    assert "workflow_json" not in metadata
    # The workflow that was submitted carries the seed and the prompt the row records.
    submitted = harness.comfy.workflows[0]
    assert submitted["6"]["inputs"]["seed"] == 111
    assert submitted["3"]["inputs"]["text"] == metadata["prompt_positive"]

    types = harness.events.types()
    assert types.index("image.completed") < types.index("message.completed")
    assert [data["phase"] for kind, data in harness.events.sent if kind == "image.progress"] == [
        "prompt",
        "loading",
        "sampling",
        "saving",
    ]
    completed = next(data for kind, data in harness.events.sent if kind == "image.completed")
    assert (completed["message_id"], completed["image"]["id"]) == (reply.id, reply.image_id)
    assert harness.media.read_staged(done.id) is None


@pytest.mark.anyio
async def test_persistence_is_idempotent_for_a_job_that_restarts(harness: Harness, media_root: Path) -> None:
    conversation_id = conversation_of(harness.engine)
    sent = harness.images.request(conversation_id, REQUEST)
    await harness.run_stage(RequiredMode.LLM)
    await harness.run_stage(RequiredMode.IMAGE)
    persisting = harness.job(sent.job.id)
    assert persisting.stage == "persist"
    context = RunContext(lease=None, genkey="g")

    first = await harness.service(persisting, context)
    second = await harness.service(persisting, context)  # A restart ran the stage again after its commit.

    assert first == second
    assert scalar(harness.engine, "SELECT count(*) FROM generated_images") == 1
    assert [path.suffix for path in files(media_root, "images")] == [".png"]
    assert [path.suffix for path in files(media_root, "thumbs")] == [".webp"]
    assert scalar(harness.engine, "SELECT count(*) FROM message_variants WHERE image_id IS NOT NULL") == 1
    assert scalar(harness.engine, "SELECT status FROM messages WHERE id = :id", id=sent.reply.id) == "complete"


@pytest.mark.anyio
async def test_a_staged_file_that_is_not_an_image_leaves_no_image_and_fails_the_message(
    harness: Harness, media_root: Path
) -> None:
    conversation_id = conversation_of(harness.engine)
    sent = harness.images.request(conversation_id, REQUEST)
    await harness.run_stage(RequiredMode.LLM)
    await harness.run_stage(RequiredMode.IMAGE)
    persisting = harness.job(sent.job.id)
    harness.media.stage_job_file(persisting.id, b"not an image")

    with pytest.raises(JobFailedError) as failure:
        await harness.service(persisting, RunContext(lease=None, genkey="g"))

    assert (failure.value.code, failure.value.retryable) == ("INTERNAL", False)
    assert scalar(harness.engine, "SELECT count(*) FROM generated_images") == 0
    assert files(media_root, "images") == []
    assert files(media_root, "thumbs") == []
    assert scalar(harness.engine, "SELECT status FROM messages WHERE id = :id", id=sent.reply.id) == "failed"
    variant_status = scalar(
        harness.engine, "SELECT status FROM message_variants WHERE id = :id", id=sent.reply.active_variant_id
    )
    assert variant_status == "failed"
    assert ("message.failed", {"message_id": sent.reply.id, "error_code": "INTERNAL"}) in harness.events.sent
    assert harness.media.read_staged(persisting.id) is None


@pytest.mark.anyio
async def test_images_load_with_comfyui_off(harness: Harness, settings: Settings) -> None:
    conversation_id = conversation_of(harness.engine)
    harness.images.request(conversation_id, REQUEST)
    done = await harness.run_all()
    assert done.result is not None
    image_id = done.result["image_id"]

    harness.comfy.off = True
    app = create_app(settings, harness.engine, media_store=harness.media)
    client = TestClient(app, base_url="http://localhost:4200", raise_server_exceptions=False)

    original = client.get(f"/media/images/{image_id}/original")
    assert (original.status_code, original.headers["content-type"]) == (200, "image/png")
    with Image.open(io.BytesIO(original.content)) as image:
        assert image.size == SIZE
    thumbnail = client.get(f"/media/images/{image_id}/thumbnail")
    assert (thumbnail.status_code, thumbnail.headers["content-type"]) == (200, "image/webp")
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        assert max(image.size) <= 512

    metadata = client.get(f"/api/v1/images/{image_id}")
    assert metadata.status_code == 200
    assert metadata.json()["url"] == f"/media/images/{image_id}/original"
    listed = client.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert [item["image_id"] for item in listed.json()["items"]] == [None, image_id]
    assert client.get("/media/images/unknown/original").status_code == 404


@pytest.mark.anyio
async def test_regeneration_adds_a_variant_with_a_new_seed_and_skips_the_prompt(harness: Harness) -> None:
    conversation_id = conversation_of(harness.engine)
    harness.images.request(conversation_id, REQUEST)
    first = await harness.run_all()
    assert first.result is not None
    original = harness.images.get(first.result["image_id"])
    text_calls = len(harness.text.calls)

    regeneration = harness.images.regenerate(original["id"])
    job = harness.job(regeneration.job.id)
    assert (job.stage, job.required_mode) == ("render", RequiredMode.IMAGE)
    assert regeneration.message.status is MessageStatus.QUEUED
    assert regeneration.message.active_variant_id == regeneration.variant_id

    await harness.run_stage(RequiredMode.IMAGE)
    done = await harness.run_stage(RequiredMode.NONE)
    assert done.status is JobStatus.COMPLETED
    assert len(harness.text.calls) == text_calls

    messages = MessageService(harness.engine).history(conversation_id).items
    message = messages[-1]
    assert (message.variant_count, message.status, message.content) == (2, MessageStatus.COMPLETE, CAPTION)
    assert message.active_variant_id == regeneration.variant_id
    assert message.image_id is not None
    assert message.image_id != original["id"]
    renewed = harness.images.get(message.image_id)
    assert (renewed["prompt_positive"], renewed["scene"], renewed["aspect"]) == (
        original["prompt_positive"],
        original["scene"],
        original["aspect"],
    )
    assert renewed["seed"] == 222
    assert original["seed"] == 111
    # The first variant keeps its image, so the arrows still switch between the two.
    assert scalar(harness.engine, "SELECT count(*) FROM generated_images") == 2
    variants = MessageService(harness.engine).variants(message.id)
    assert [variant.content for variant in variants] == [CAPTION, CAPTION]

    kept = harness.images.regenerate(message.image_id, keep_seed=True)
    await harness.run_stage(RequiredMode.IMAGE)
    await harness.run_stage(RequiredMode.NONE)
    assert kept.message.variant_count == 3
    third = MessageService(harness.engine).history(conversation_id).items[-1]
    assert third.image_id is not None
    assert harness.images.get(third.image_id)["seed"] == 222


def test_a_refused_request_saves_nothing(harness: Harness) -> None:
    conversation_id = conversation_of(harness.engine)
    images = harness.images
    with pytest.raises(ValidationFailedError):
        images.request(conversation_id, "   ")
    with pytest.raises(ValidationFailedError):
        images.request(conversation_id, REQUEST, aspect="tiny")
    with pytest.raises(ValidationFailedError):
        images.request(conversation_id, REQUEST, quality="ultra")
    with pytest.raises(ValidationFailedError):
        images.request(conversation_id, REQUEST, seed=-1)
    with pytest.raises(ValidationFailedError):
        images.request(conversation_id, REQUEST, outfit="gala")
    assert scalar(harness.engine, "SELECT count(*) FROM messages") == 0
    assert scalar(harness.engine, "SELECT count(*) FROM generation_jobs") == 0
