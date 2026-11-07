"""P05.7: the scheduler routes an `image_request` through the orchestrator's mode switch on a migrated database.

`Gpu` plays the control API: it grants leases only in the ready state of their mode, applies a requested mode only
when the test calls `apply` and no lease is held, and stops one runtime before it starts the other, as the lifecycle
manager does (its own tests, orchestrator/tests/integration/test_image_lifecycle.py, cover the release verification).
Its `blocked` reports a load held back for VRAM, as the orchestrator does in IDLE. The job runs its real stages with
the fake text runtime and the fake ComfyUI of test_image_job.py.

P06.2 adds the backend side of T-ORC-05: the job that waits for a mode held back for VRAM fails after the wait, and
the next one waits anew. The orchestrator side is orchestrator/tests/unit/test_vram_guard.py.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest
from sqlalchemy import Engine

from integration.test_image_job import REQUEST, Harness, conversation_of
from lyrveil.domain.messages import MessageStatus
from lyrveil.jobs.handlers import JobDone, JobHandler, JobOutcome, RunContext
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, write
from lyrveil.media.store import MediaStore
from lyrveil.orchestration.client import (
    BlockedVram,
    DesiredMode,
    GpuStatus,
    Lease,
    LeaseMode,
    LeaseOutcome,
    LeaseRefusedError,
    OrchestratorUnavailableError,
)
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.messages import MessageService

READY: Mapping[LeaseMode, str] = {"llm": "LLM_READY", "image": "IMAGE_READY"}
GENERATING: Mapping[LeaseMode, str] = {"llm": "GENERATING_TEXT", "image": "GENERATING_IMAGE"}


class Gpu:
    """A ModeSwitch and LeaseProvider over one scripted GPU, ready in `llm` at first.

    `requests` lists each (mode, job_id) asked for, `leases` each (mode, job_id) granted, and `runtime_log` each stop
    and start in order. `running` holds the runtimes running; a start while another runs is recorded in `violations`.
    `reads` counts the state reads, and `down` makes every call fail as an unreachable orchestrator. While `blocked`
    is set, the state is IDLE with GPU_INSUFFICIENT_VRAM and those numbers.
    """

    def __init__(self) -> None:
        self.mode: LeaseMode = "llm"
        self.desired: DesiredMode = "llm"
        self.lease: Lease | None = None
        self.running: set[LeaseMode] = {"llm"}
        self.requests: list[tuple[DesiredMode, str | None]] = []
        self.leases: list[tuple[LeaseMode, str]] = []
        self.runtime_log: list[tuple[str, LeaseMode]] = []
        self.violations: list[LeaseMode] = []
        self.reads = 0
        self.down = False
        self.blocked: BlockedVram | None = None

    def state(self) -> str:
        return (GENERATING if self.lease is not None else READY)[self.mode]

    def read_state(self) -> GpuStatus:
        self._reachable()
        self.reads += 1
        if self.blocked is not None:
            return GpuStatus(
                state="IDLE",
                desired_mode=self.desired,
                draining=False,
                blocked_reason="GPU_INSUFFICIENT_VRAM",
                blocked_vram=self.blocked,
            )
        return GpuStatus(state=self.state(), desired_mode=self.desired, draining=False)

    def request_mode(self, mode: DesiredMode, job_id: str | None = None) -> None:
        self._reachable()
        self.requests.append((mode, job_id))
        self.desired = mode

    def apply(self) -> None:
        """Moves to the desired mode, as the lifecycle manager does once no lease is held."""
        if self.lease is not None or self.desired in (self.mode, "none"):
            return
        target: LeaseMode = "image" if self.desired == "image" else "llm"
        self.running.discard(self.mode)
        self.runtime_log.append(("stop", self.mode))
        if self.running:
            self.violations.append(target)
        self.running.add(target)
        self.runtime_log.append(("start", target))
        self.mode = target

    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease:
        self._reachable()
        if self.lease is not None:
            raise LeaseRefusedError("LEASE_HELD")
        if mode != self.mode or self.desired != mode:
            raise LeaseRefusedError("NOT_READY")
        self.lease = Lease(id=f"lease-{job_id}-{mode}", mode=mode, job_id=job_id, ttl_s=30, expires_in_s=30.0)
        self.leases.append((mode, job_id))
        return self.lease

    def renew_lease(self, lease: Lease) -> Lease:
        return lease

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        self.lease = None
        return True

    def _reachable(self) -> None:
        if self.down:
            raise OrchestratorUnavailableError("the control API did not answer")


class Done:
    """A handler that finishes every job at once and records the ids it ran."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        self.ran.append(job.id)
        return JobDone()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Clock:
    """The scheduler's monotonic clock, moved only by the test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def scheduler(harness: Harness, gpu: Gpu, other: Done, clock: Clock | None = None) -> JobScheduler:
    handlers: dict[JobKind, JobHandler] = {
        JobKind.IMAGE_REQUEST: harness.service,
        JobKind.CHAT_REPLY: other,
        JobKind.MEMORY_EXTRACTION: other,
    }

    def unused() -> RequiredMode:
        raise AssertionError("with a mode switch the scheduler must read the mode from the orchestrator")

    return JobScheduler(harness.engine, harness.queue, handlers, gpu, unused, mode_switch=gpu, clock=clock or Clock())


def enqueue(engine: Engine, queue: JobQueue, kind: JobKind) -> Job:
    return write(engine, lambda connection: queue.enqueue(connection, kind))


async def ran(worker: JobScheduler) -> Job:
    """Runs one pass and returns the job it ran, failing the test when the pass ran none."""
    job = await worker.run_once()
    assert job is not None
    return job


@pytest.fixture
def harness(engine: Engine, tmp_path: Path) -> Harness:
    upgrade_to_head(engine)
    return Harness(engine, MediaStore(tmp_path / "media"), [111, 222, 333, 444])


@pytest.mark.anyio
async def test_an_image_job_switches_to_image_mode_and_back(harness: Harness) -> None:
    gpu, other = Gpu(), Done()
    worker = scheduler(harness, gpu, other)
    sent = harness.images.request(conversation_of(harness.engine), REQUEST)
    job_id = sent.job.id

    # The prompt stage is text work, run in the llm mode the GPU starts in.
    prompted = await ran(worker)
    assert (prompted.stage, prompted.required_mode) == ("render", RequiredMode.IMAGE)
    assert gpu.requests == []

    # The render stage needs the image mode: the scheduler asks for it once, on the job's behalf, and runs nothing
    # until the orchestrator reports the image runtime ready.
    assert await worker.run_once() is None
    assert await worker.run_once() is None
    assert gpu.requests == [("image", job_id)]
    gpu.apply()

    rendered = await ran(worker)
    assert (rendered.stage, rendered.required_mode) == ("persist", RequiredMode.NONE)

    # Nothing left needs the image runtime, so the desired mode returns to llm while `persist`, which needs no GPU,
    # runs in the same pass.
    done = await ran(worker)
    assert (done.id, done.status) == (job_id, JobStatus.COMPLETED)
    assert gpu.requests == [("image", job_id), ("llm", None)]
    gpu.apply()

    # With llm asked for and nothing queued, an idle pass does not read the GPU state again.
    reads = gpu.reads
    assert await worker.run_once() is None
    assert gpu.reads == reads

    assert gpu.leases == [("llm", job_id), ("image", job_id)]
    assert gpu.runtime_log == [("stop", "llm"), ("start", "image"), ("stop", "image"), ("start", "llm")]
    assert gpu.violations == []
    assert len(harness.comfy.workflows) == 1


@pytest.mark.anyio
async def test_foreground_text_goes_before_an_image_and_background_work_waits_for_it(harness: Harness) -> None:
    gpu, other = Gpu(), Done()
    worker = scheduler(harness, gpu, other)
    sent = harness.images.request(conversation_of(harness.engine), REQUEST)
    await worker.run_once()  # The prompt stage; the job now waits in `render`.
    reply = enqueue(harness.engine, harness.queue, JobKind.CHAT_REPLY)
    background = enqueue(harness.engine, harness.queue, JobKind.MEMORY_EXTRACTION)

    # A queued reply is foreground text work: it runs first, and the mode stays llm.
    assert (await ran(worker)).id == reply.id
    assert gpu.requests == []

    # Then the image goes before the background job, which waits in image mode without asking for llm.
    assert await worker.run_once() is None
    assert gpu.requests == [("image", sent.job.id)]
    gpu.apply()
    assert (await ran(worker)).id == sent.job.id
    assert other.ran == [reply.id]

    # With no image left, the background job's need for llm brings the mode back while the image is persisted, and
    # the background job then runs in llm mode.
    assert (await ran(worker)).status is JobStatus.COMPLETED
    assert gpu.requests == [("image", sent.job.id), ("llm", background.id)]
    gpu.apply()
    assert (await ran(worker)).id == background.id
    assert gpu.violations == []


@pytest.mark.anyio
async def test_an_unreachable_orchestrator_holds_gpu_work_but_not_work_without_a_gpu(harness: Harness) -> None:
    gpu, other = Gpu(), Done()
    worker = scheduler(harness, gpu, other)
    sent = harness.images.request(conversation_of(harness.engine), REQUEST)
    await worker.run_once()
    await worker.run_once()
    gpu.apply()
    await worker.run_once()  # The render; the job now waits in `persist`.

    gpu.down = True
    persisted = await ran(worker)
    assert (persisted.id, persisted.status) == (sent.job.id, JobStatus.COMPLETED)
    reply = enqueue(harness.engine, harness.queue, JobKind.CHAT_REPLY)
    assert await worker.run_once() is None

    gpu.down = False
    assert await worker.run_once() is None  # Asks for llm; the image runtime is still the one loaded.
    gpu.apply()
    assert (await ran(worker)).id == reply.id
    assert gpu.requests == [("image", sent.job.id), ("llm", reply.id)]


@pytest.mark.anyio
async def test_an_image_held_back_for_vram_fails_after_the_wait_and_the_next_one_waits_anew(harness: Harness) -> None:
    gpu, other, clock = Gpu(), Done(), Clock()
    worker = scheduler(harness, gpu, other, clock)
    conversation_id = conversation_of(harness.engine)
    first = harness.images.request(conversation_id, REQUEST)
    await ran(worker)  # The prompt stage; the job now waits in `render`.
    assert await worker.run_once() is None
    assert gpu.requests == [("image", first.job.id)]

    # Another program holds the memory, so the orchestrator keeps the GPU in IDLE. The job waits 120 s from the first
    # pass that sees the hold, and nothing else is asked of the GPU meanwhile.
    gpu.blocked = BlockedVram(mode="image", free_mib=5282, required_mib=6500)
    assert await worker.run_once() is None
    clock.now += 119.0
    assert await worker.run_once() is None
    assert harness.job(first.job.id).status is JobStatus.QUEUED

    clock.now += 1.0
    failed = await ran(worker)
    assert (failed.id, failed.status, failed.error_code) == (first.job.id, JobStatus.FAILED, "GPU_INSUFFICIENT_VRAM")
    assert failed.error_detail is not None
    assert "6500 MiB" in failed.error_detail
    assert "5282" in failed.error_detail
    # The image's message ends failed and says so, as for a failed render, and nothing reached ComfyUI.
    shown = {message.id: message.status for message in MessageService(harness.engine).history(conversation_id).items}
    assert shown[first.reply.id] is MessageStatus.FAILED
    assert ("message.failed", {"message_id": first.reply.id, "error_code": "GPU_INSUFFICIENT_VRAM"}) in (
        harness.events.sent
    )
    assert (gpu.leases, harness.comfy.workflows) == ([("llm", first.job.id)], [])

    # With nothing left that needs the image runtime, the next pass asks for llm, which the freed card serves.
    assert await worker.run_once() is None
    assert gpu.requests == [("image", first.job.id), ("llm", None)]
    gpu.blocked = None

    # The next image gets a whole wait of its own, and renders once the memory is free before that wait ends.
    second = harness.images.request(conversation_id, REQUEST)
    await ran(worker)
    assert await worker.run_once() is None
    assert gpu.requests[-1] == ("image", second.job.id)
    gpu.blocked = BlockedVram(mode="image", free_mib=6000, required_mib=6500)
    assert await worker.run_once() is None
    clock.now += 60.0
    assert await worker.run_once() is None
    assert harness.job(second.job.id).status is JobStatus.QUEUED
    gpu.blocked = None
    gpu.apply()
    rendered = await ran(worker)
    assert (rendered.id, rendered.stage) == (second.job.id, "persist")
    assert gpu.violations == []
