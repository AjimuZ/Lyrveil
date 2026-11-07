"""T-ORC-08, backend side: the launch levels of docs/gpu-orchestration.md (Load failures and launch levels) on a
migrated database, through the real JobScheduler and GpuStateFollower over a launch file in a temporary folder.

- A reply that runs out of memory lowers the level before its lease is released with `oom`, so the orchestrator's
  restart of the text runtime reads the new file, and the reply is queued again.
- A failed load the orchestrator classified as out of memory, read by the follower, lowers the level once.
- After the image runtime's last failed load the orchestrator names the job: it ends with IMAGE_LOAD_FAILED, and the
  next image job asks for the mode again.

The orchestrator's side, the classification and `load_failure`, is in orchestrator/tests/unit/test_recovery_table.py.
"""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from lyrveil.catalog import load_catalog
from lyrveil.jobs.handlers import JobFailedError, JobOutcome, RunContext
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import IMAGE_LOAD_FAILED, JobScheduler, write
from lyrveil.orchestration.client import DesiredMode, GpuStatus, Lease, LeaseMode, LeaseOutcome, LoadFailure
from lyrveil.orchestration.gpu_state import GpuStateFollower
from lyrveil.orchestration.launch_levels import LaunchLevels
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import RuntimeOutOfMemoryError

BOOT = "0192f3a0-0000-7000-8000-000000000000"
LAUNCH_FILE = (
    "MODEL_PROFILE=gemma-4-26b-a4b\n"
    "MODEL_FILE=gemma-4-26B_q4_0-it.gguf\n"
    "CONTEXT_SIZE=16384\n"
    "GPU_LAYERS=-1\n"
    "BATCH_SIZE=512\n"
    "MOE_CPU_LAYERS=0\n"
    "USE_MMAP=0\n"
    "KV_QUANT=f16\n"
    "SWA_PADDING=0\n"
    "SMART_CACHE=0\n"
    "LEVEL=A\n"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def find_catalog() -> Path:
    """Returns the shipped catalog in the repository or, in the test image, in /app/config."""
    here = Path(__file__).resolve()
    for root in (here.parents[3], here.parents[2]):
        candidate = root / "config" / "model-catalog.json"
        if candidate.is_file():
            return candidate
    raise AssertionError("model-catalog.json is in neither candidate config folder")


def values(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


@pytest.fixture
def launch_file(tmp_path: Path) -> Path:
    path = tmp_path / "runtime" / "llm-launch.env"
    path.parent.mkdir()
    path.write_text(LAUNCH_FILE, encoding="utf-8")
    return path


@pytest.fixture
def levels(launch_file: Path) -> LaunchLevels:
    return LaunchLevels(load_catalog(find_catalog()), launch_file)


class Leases:
    """Grants every lease and records, for each release, its outcome and the launch file's LEVEL at that moment."""

    def __init__(self, launch_file: Path) -> None:
        self.launch_file = launch_file
        self.released: list[tuple[LeaseOutcome, str]] = []

    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease:
        return Lease(id=f"lease-{job_id}", mode=mode, job_id=job_id, ttl_s=30, expires_in_s=30.0)

    def renew_lease(self, lease: Lease) -> Lease:
        return lease

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        self.released.append((outcome, values(self.launch_file)["LEVEL"]))
        return True


class OutOfMemoryReply:
    """A reply handler whose generation always runs out of CUDA memory."""

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        raise RuntimeOutOfMemoryError("out of memory")


def enqueue(engine: Engine, queue: JobQueue, kind: JobKind, stage: str | None = None) -> Job:
    return write(engine, lambda connection: queue.enqueue(connection, kind, stage=stage))


@pytest.mark.anyio
async def test_a_reply_out_of_memory_lowers_the_level_before_its_lease_is_released(
    engine: Engine, launch_file: Path, levels: LaunchLevels
) -> None:
    upgrade_to_head(engine)
    queue = JobQueue()
    leases = Leases(launch_file)
    scheduler = JobScheduler(
        engine,
        queue,
        {JobKind.CHAT_REPLY: OutOfMemoryReply()},
        leases,
        lambda: RequiredMode.LLM,
        launch_levels=levels,
    )
    enqueue(engine, queue, JobKind.CHAT_REPLY)

    retried = await scheduler.run_once()
    assert retried is not None
    assert (retried.status, retried.error_code) == (JobStatus.QUEUED, "LLM_OOM")
    # The orchestrator restarts the runtime when the lease ends with `oom`: by then the file holds level B.
    assert leases.released == [("oom", "B")]
    assert (values(launch_file)["CONTEXT_SIZE"], values(launch_file)["BATCH_SIZE"]) == ("12288", "512")


@pytest.mark.anyio
async def test_a_reply_without_launch_levels_still_releases_its_lease_with_oom(
    engine: Engine, launch_file: Path
) -> None:
    upgrade_to_head(engine)
    queue = JobQueue()
    leases = Leases(launch_file)
    scheduler = JobScheduler(engine, queue, {JobKind.CHAT_REPLY: OutOfMemoryReply()}, leases, lambda: RequiredMode.LLM)
    enqueue(engine, queue, JobKind.CHAT_REPLY)
    assert await scheduler.run_once() is not None
    assert leases.released == [("oom", "A")]


class States:
    """A GpuStateSource that answers each long poll with the next scripted state, then repeats the last one."""

    def __init__(self, *states: GpuStatus) -> None:
        self.states = list(states)

    def wait_for_state(self, since: int, wait_s: float) -> GpuStatus:
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


class Events:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Mapping[str, Any]]] = []

    def publish(self, event_type: str, data: Mapping[str, Any]) -> int:
        self.sent.append((event_type, data))
        return len(self.sent)


def failed_load(seq: int, *, failures: int = 1) -> GpuStatus:
    failure = LoadFailure(
        seq=seq, mode="llm", code="LLM_LOAD_FAILED", oom=True, failures=failures, final=failures >= 3, job_id=None
    )
    return GpuStatus(
        state="RECOVERING", desired_mode="llm", draining=False, seq=seq, boot_id=BOOT, load_failure=failure
    )


@pytest.mark.anyio
async def test_a_load_out_of_memory_read_by_the_follower_lowers_the_level_once_per_failure(
    launch_file: Path, levels: LaunchLevels
) -> None:
    # The failure stays in the state document until the text runtime is ready again.
    idle = replace(failed_load(8), state="IDLE", seq=9)
    retrying = replace(failed_load(8), state="LOADING_LLM", seq=10)
    source = States(failed_load(8), idle, retrying, failed_load(11, failures=2))
    events = Events()
    follower = GpuStateFollower(source, events, lambda: 1, on_state=levels.observe)

    assert await follower.follow_once() is not None
    assert values(launch_file)["LEVEL"] == "B"
    # Later states that still carry the same failure, such as the retry's load, change nothing more.
    await follower.follow_once()
    await follower.follow_once()
    assert values(launch_file)["LEVEL"] == "B"
    # The retry at level B fails too: the next failure lowers to C.
    await follower.follow_once()
    assert (values(launch_file)["LEVEL"], values(launch_file)["CONTEXT_SIZE"]) == ("C", "8192")
    assert [data["state"] for kind, data in events.sent if kind == "gpu.state"] == [
        "RECOVERING",
        "IDLE",
        "LOADING_LLM",
        "RECOVERING",
    ]


class Switch:
    """A ModeSwitch whose state the test sets, recording each mode request."""

    def __init__(self, status: GpuStatus) -> None:
        self.status = status
        self.requests: list[tuple[DesiredMode, str | None]] = []

    def read_state(self) -> GpuStatus:
        return self.status

    def request_mode(self, mode: DesiredMode, job_id: str | None = None) -> None:
        self.requests.append((mode, job_id))


class ImageHandler:
    """An image handler that must never run a stage here, and records the jobs it was asked to abandon."""

    def __init__(self) -> None:
        self.abandoned: list[tuple[str, str]] = []

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        raise AssertionError("no image stage runs while its runtime cannot load")

    async def abandon(self, job: Job, error: JobFailedError) -> None:
        self.abandoned.append((job.id, error.code))


@pytest.mark.anyio
async def test_the_image_job_named_by_the_last_failed_image_load_ends_and_the_next_one_asks_again(
    engine: Engine, launch_file: Path
) -> None:
    upgrade_to_head(engine)
    queue = JobQueue()
    first = enqueue(engine, queue, JobKind.IMAGE_REQUEST, stage="render")
    second = enqueue(engine, queue, JobKind.IMAGE_REQUEST, stage="render")
    failure = LoadFailure(
        seq=11, mode="image", code="IMAGE_LOAD_FAILED", oom=False, failures=3, final=True, job_id=first.id
    )
    switch = Switch(
        GpuStatus(state="LLM_READY", desired_mode="llm", draining=False, seq=14, boot_id=BOOT, load_failure=failure)
    )
    handler = ImageHandler()
    scheduler = JobScheduler(
        engine,
        queue,
        {JobKind.IMAGE_REQUEST: handler},
        Leases(launch_file),
        lambda: RequiredMode.LLM,
        mode_switch=switch,
    )

    ended = await scheduler.run_once()
    assert ended is not None
    assert (ended.id, ended.status, ended.error_code) == (first.id, JobStatus.FAILED, IMAGE_LOAD_FAILED)
    assert ended.error_detail is not None
    assert handler.abandoned == [(first.id, IMAGE_LOAD_FAILED)]
    assert switch.requests == []

    # The failure still names the first job, which has ended: the second asks for the image mode afresh.
    assert await scheduler.run_once() is None
    assert switch.requests == [("image", second.id)]
    assert handler.abandoned == [(first.id, IMAGE_LOAD_FAILED)]
