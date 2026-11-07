"""T-IMG-11 on a migrated database: an image that runs out of memory is retried with the tiled workflow of the same
preset, then, for **Alta**, tried once with **Rapida**, and the stored preset is the one that produced the file
(docs/image-generation.md, Failures).

The `image_request` job runs through the real scheduler and ImageGenerationService with a fake text runtime and a fake
ComfyUI that runs out of memory a scripted number of times. The queue's clock moves only when the test passes a
retry's 10 s backoff, and the leases record the outcome each run released them with: `oom` makes the orchestrator
restart the runtime before the next try.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from integration.test_image_caption import ImageRuntime as FakeText
from integration.test_image_job import CAPTION, REQUEST, Events, FakeComfy, conversation_of
from integration.test_job_cancellation import QueueClock
from integration.test_reply_streaming import Leases
from lyrveil.domain.messages import MessageStatus
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler
from lyrveil.media.store import MediaStore
from lyrveil.orchestration.client import Lease, LeaseOutcome
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.image_runtime import ImageEvent, ImageOutOfMemoryError, ImageRequest
from lyrveil.services.image_generation import ImageGenerationService
from lyrveil.services.images import ImageService
from lyrveil.services.messages import MessageService

# docs/backend.md, Retry policy: a failed render waits 10 s.
RENDER_BACKOFF_S = 10


class OutOfMemoryComfy(FakeComfy):
    """A FakeComfy whose next `failures` renders run out of memory after the workflow is submitted."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def render(self, request: ImageRequest) -> AsyncIterator[ImageEvent]:
        if self.failures > 0:
            self.failures -= 1
            self.workflows.append(request.workflow)
            raise ImageOutOfMemoryError("OutOfMemoryError: CUDA out of memory")
        async for event in super().render(request):
            yield event


class RecordingLeases(Leases):
    """Grants every lease and records each release's mode and outcome."""

    def __init__(self) -> None:
        self.released: list[tuple[str, LeaseOutcome]] = []

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        self.released.append((lease.mode, outcome))
        return True


class Harness:
    """The image service and the scheduler over one queue whose clock the test moves."""

    def __init__(self, engine: Engine, media: MediaStore, failures: int) -> None:
        self.engine = engine
        self.clock = QueueClock()
        self.queue = JobQueue(self.clock)
        self.comfy = OutOfMemoryComfy(failures)
        self.events = Events()
        self.leases = RecordingLeases()
        self.mode = RequiredMode.LLM
        service = ImageGenerationService(
            engine, FakeText(CAPTION), self.comfy, media, self.events, seed_source=lambda: 111
        )
        self.scheduler = JobScheduler(
            engine, self.queue, {JobKind.IMAGE_REQUEST: service}, self.leases, lambda: self.mode
        )
        self.images = ImageService(engine, media, self.queue, seed_source=lambda: 111)

    async def run(self, mode: RequiredMode) -> Job:
        self.mode = mode
        job = await self.scheduler.run_once()
        assert job is not None
        return job

    async def render_after_backoff(self) -> Job:
        """Runs the render again once its backoff has passed; before that it is not eligible."""
        self.mode = RequiredMode.IMAGE
        assert await self.scheduler.run_once() is None
        self.clock.advance(RENDER_BACKOFF_S)
        return await self.run(RequiredMode.IMAGE)

    def graphs(self) -> list[tuple[str, bool]]:
        """Returns the preset and whether the decode is tiled for each workflow submitted, in order."""
        return [_preset_of(workflow) for workflow in self.comfy.workflows]

    def failed_events(self) -> list[str]:
        return [kind for kind, _ in self.events.sent if kind == "message.failed"]


def _preset_of(workflow: dict[str, Any]) -> tuple[str, bool]:
    """Alta samples twice and Rapida once; a tiled file decodes with VAEDecodeTiled."""
    classes = [node["class_type"] for node in workflow.values()]
    return ("alta" if classes.count("KSampler") == 2 else "rapida"), "VAEDecodeTiled" in classes


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make(engine: Engine, tmp_path: Path, failures: int) -> Harness:
    upgrade_to_head(engine)
    return Harness(engine, MediaStore(tmp_path / "data" / "media"), failures)


@pytest.mark.anyio
async def test_alta_out_of_memory_twice_ends_with_one_rapida_try_that_is_stored_as_rapida(
    engine: Engine, tmp_path: Path
) -> None:
    harness = make(engine, tmp_path, failures=2)
    conversation_id = conversation_of(engine)
    sent = harness.images.request(conversation_id, REQUEST)
    assert (await harness.run(RequiredMode.LLM)).stage == "render"

    first = await harness.run(RequiredMode.IMAGE)
    assert (first.status, first.stage, first.error_code, first.attempt) == (JobStatus.QUEUED, "render", "IMAGE_OOM", 1)
    second = await harness.render_after_backoff()
    # The tiled retry used the kind's last attempt: the Rapida try starts its own count, with the preset changed.
    assert (second.status, second.error_code, second.attempt) == (JobStatus.QUEUED, "IMAGE_OOM", 0)
    assert (second.payload["quality"], second.payload["fallback_from"]) == ("rapida", "alta")
    rendered = await harness.render_after_backoff()
    assert (rendered.status, rendered.stage) == (JobStatus.QUEUED, "persist")
    done = await harness.run(RequiredMode.NONE)
    assert done.status is JobStatus.COMPLETED

    assert harness.graphs() == [("alta", False), ("alta", True), ("rapida", True)]
    assert {workflow["6"]["inputs"]["seed"] for workflow in harness.comfy.workflows} == {111}
    # Each out of memory ended its lease with `oom`, so the orchestrator restarted the runtime before the next try.
    assert harness.leases.released == [("llm", "ok"), ("image", "oom"), ("image", "oom"), ("image", "ok")]

    reply = MessageService(engine).history(conversation_id).items[1]
    assert (reply.id, reply.status, reply.content) == (sent.reply.id, MessageStatus.COMPLETE, CAPTION)
    assert reply.image_id is not None
    metadata = harness.images.get(reply.image_id)
    assert (metadata["quality_preset"], metadata["workflow_id"]) == ("rapida", "sdxl_base_tiled.v2.json")
    assert (metadata["hires_denoise"], metadata["hires_scale"], metadata["hires_steps"]) == (None, None, None)
    assert harness.failed_events() == []


@pytest.mark.anyio
async def test_the_rapida_try_runs_once(engine: Engine, tmp_path: Path) -> None:
    harness = make(engine, tmp_path, failures=3)
    conversation_id = conversation_of(engine)
    sent = harness.images.request(conversation_id, REQUEST)
    await harness.run(RequiredMode.LLM)
    await harness.run(RequiredMode.IMAGE)
    await harness.render_after_backoff()
    failed = await harness.render_after_backoff()

    assert (failed.status, failed.error_code) == (JobStatus.FAILED, "IMAGE_OOM")
    assert harness.graphs() == [("alta", False), ("alta", True), ("rapida", True)]
    harness.clock.advance(3600)
    harness.mode = RequiredMode.IMAGE
    assert await harness.scheduler.run_once() is None
    reply = MessageService(engine).history(conversation_id).items[1]
    assert (reply.id, reply.status) == (sent.reply.id, MessageStatus.FAILED)
    assert harness.failed_events() == ["message.failed"]


@pytest.mark.anyio
async def test_a_rapida_request_gets_the_tiled_retry_and_no_other_try(engine: Engine, tmp_path: Path) -> None:
    harness = make(engine, tmp_path, failures=2)
    conversation_id = conversation_of(engine)
    harness.images.request(conversation_id, REQUEST, quality="rapida")
    await harness.run(RequiredMode.LLM)
    await harness.run(RequiredMode.IMAGE)
    failed = await harness.render_after_backoff()

    assert (failed.status, failed.error_code) == (JobStatus.FAILED, "IMAGE_OOM")
    assert harness.graphs() == [("rapida", False), ("rapida", True)]
    assert "fallback_from" not in failed.payload
    assert harness.failed_events() == ["message.failed"]
