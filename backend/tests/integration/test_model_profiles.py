"""T-MOD-03 and T-MOD-04: profile changes and custom registration (docs/models.md, Selecting a profile and Custom
profiles; docs/security.md, Model files), on a migrated database, through the /settings/models routes and the real
JobScheduler, over a models folder and a launch file in a temporary folder.

The catalog is the shipped one with small sizes, so its files can be written here. The orchestrator is a fake that
plays its text side: a desired mode `none` stops the runtime and reports the verified release as IDLE, and `llm` loads
the launch file as it is at that moment, which the fake records. The orchestrator's own part, the stop with its release
verified and the start that follows, is in orchestrator/tests/integration/test_profile_change.py.
"""

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from integration.test_launch_levels import find_catalog, values
from lyrveil.catalog import Catalog, Tier, parse_catalog
from lyrveil.jobs.handlers import JobDone, JobOutcome, RunContext
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, write
from lyrveil.main import create_app
from lyrveil.model_files import ModelFileRefusedError, ModelFiles
from lyrveil.orchestration.client import DesiredMode, GpuStatus, Lease, LeaseMode, LeaseOutcome
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.persistence.settings import LLM_PROFILE_ID, MODELS_CUSTOM, get_value
from lyrveil.services.model_maintenance import MODEL_FILE_MISSING, ModelMaintenance
from lyrveil.services.model_profiles import ModelProfiles, machine_tier
from lyrveil.settings import Settings

API = "/api/v1"
BOOT = "0192f3a0-0000-7000-8000-000000000000"
CUSTOM_TEXT = "my-model.Q4_K_M.gguf"
CUSTOM_IMAGE = "my-checkpoint.safetensors"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def catalog() -> Catalog:
    """The shipped catalog with every size set to a few kilobytes."""
    document = json.loads(find_catalog().read_text(encoding="utf-8"))
    for index, entry in enumerate(document["text"] + document["image"]):
        entry["bytes"] = 1000 + index
    return parse_catalog(document)


@pytest.fixture
def models(tmp_path: Path) -> Path:
    root = tmp_path / "models"
    (root / "llm").mkdir(parents=True)
    (root / "image" / "checkpoints").mkdir(parents=True)
    return root


@pytest.fixture
def launch_file(tmp_path: Path) -> Path:
    return tmp_path / "data" / "runtime" / "llm-launch.env"


def place(models: Path, catalog: Catalog, *profile_ids: str) -> None:
    """Writes the file of each catalog profile with its catalog size."""
    for profile in (*catalog.text, *catalog.image):
        if profile.id in profile_ids:
            folder = "llm" if profile.file.endswith(".gguf") else "image/checkpoints"
            (models / folder / profile.file).write_bytes(b"\0" * profile.bytes)


def profiles_for(engine: Engine, catalog: Catalog, models: Path, launch_file: Path, tier: Tier) -> ModelProfiles:
    upgrade_to_head(engine)
    return ModelProfiles(engine, catalog, tier, ModelFiles(models), launch_file)


def client_for(settings: Settings, engine: Engine, profiles: ModelProfiles) -> TestClient:
    return TestClient(create_app(settings, engine, model_profiles=profiles), base_url="http://localhost:4200")


class Orchestrator:
    """The orchestrator's text side for a reload, and a lease provider that grants every lease.

    `none` makes the next two long polls report the stop and then IDLE, the verified release; `llm` is accepted only
    in IDLE, and records the MODEL_FILE of the launch file, which the runtime reads when it starts."""

    def __init__(self, launch_file: Path) -> None:
        self.launch_file = launch_file
        self.status = GpuStatus(state="LLM_READY", desired_mode="llm", draining=False, seq=1, boot_id=BOOT)
        self.pending: list[GpuStatus] = []
        self.requests: list[tuple[DesiredMode, str | None]] = []
        self.loaded: list[str] = []
        self.leased: list[tuple[str, list[str]]] = []

    def _moved(self, state: str, desired: DesiredMode) -> GpuStatus:
        return replace(self.status, state=state, desired_mode=desired, seq=(self.status.seq or 0) + 1)

    def read_state(self) -> GpuStatus:
        return self.status

    def wait_for_state(self, since: int, wait_s: float) -> GpuStatus:
        if self.pending:
            self.status = self.pending.pop(0)
        return self.status

    def request_mode(self, mode: DesiredMode, job_id: str | None = None) -> None:
        self.requests.append((mode, job_id))
        if mode == "none":
            self.status = replace(self.status, desired_mode="none")
            self.pending = [self._moved("UNLOADING_LLM", "none"), self._moved("IDLE", "none")]
        elif mode == "llm":
            assert self.status.state == "IDLE", "the new file may load only after the verified release"
            self.loaded.append(values(self.launch_file)["MODEL_FILE"])
            self.status = self._moved("LLM_READY", "llm")

    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease:
        self.leased.append((job_id, list(self.loaded)))
        return Lease(id=f"lease-{job_id}", mode=mode, job_id=job_id, ttl_s=30, expires_in_s=30.0)

    def renew_lease(self, lease: Lease) -> Lease:
        return lease

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        return True


class Reply:
    """A reply handler that finishes at once."""

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        return JobDone()


def scheduler_for(engine: Engine, profiles: ModelProfiles, orchestrator: Orchestrator) -> JobScheduler:
    return JobScheduler(
        engine,
        JobQueue(),
        {JobKind.MAINTENANCE: ModelMaintenance(profiles, orchestrator), JobKind.CHAT_REPLY: Reply()},
        orchestrator,
        lambda: RequiredMode.LLM,
        mode_switch=orchestrator,
    )


def depth(engine: Engine) -> int:
    return read(engine, JobQueue().depth)


@pytest.mark.anyio
async def test_a_text_profile_change_writes_the_launch_file_and_the_reload_stops_verifies_and_loads_it(
    engine: Engine, settings: Settings, catalog: Catalog, models: Path, launch_file: Path
) -> None:
    place(models, catalog, "gemma-4-26b-a4b", "gemma-4-12b")
    profiles = profiles_for(engine, catalog, models, launch_file, "target")
    assert profiles.ensure_launch_file() is True
    assert values(launch_file)["MODEL_PROFILE"] == "gemma-4-26b-a4b"
    orchestrator = Orchestrator(launch_file)
    client = client_for(settings, engine, profiles)

    listed = client.get(f"{API}/settings/models").json()
    assert listed["tier"] == "target"
    assert listed["active"] == {"text": "gemma-4-26b-a4b", "image": "illustrious-xl-v2.0"}
    states = {item["id"]: (item["state"], item["selectable"]) for item in listed["text"]}
    assert states["gemma-4-12b"] == ("verified", True)
    assert states["orion-26b-a4b"] == ("missing", False)
    orion = next(item for item in listed["text"] if item["id"] == "orion-26b-a4b")
    assert orion["download_command"] == ".\\scripts\\lyrveil.cmd models -Model orion-26b-a4b"

    changed = client.patch(f"{API}/settings/models", json={"llm_profile_id": "gemma-4-12b"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["active"]["text"] == "gemma-4-12b"
    small = next(profile for profile in catalog.text if profile.id == "gemma-4-12b")
    assert values(launch_file) == {
        "MODEL_PROFILE": "gemma-4-12b",
        "MODEL_FILE": small.file,
        "CONTEXT_SIZE": str(small.context_size),
        "GPU_LAYERS": "-1",
        "BATCH_SIZE": str(small.batch_size),
        "MOE_CPU_LAYERS": "0",
        "USE_MMAP": "1" if small.use_mmap else "0",
        "KV_QUANT": small.kv_quant,
        "SWA_PADDING": str(small.swa_padding),
        "SMART_CACHE": str(small.smart_cache),
        "LEVEL": "A",
    }
    assert read(engine, lambda connection: get_value(connection, LLM_PROFILE_ID)) == "gemma-4-12b"
    # The change alone touches no mode: the runtime reads the file at its next load.
    assert orchestrator.requests == []

    reply = write(engine, lambda connection: JobQueue().enqueue(connection, JobKind.CHAT_REPLY))
    applied = client.post(f"{API}/settings/models/apply")
    assert applied.status_code == 202, applied.text
    job = applied.json()
    # Ahead of the reply already queued, so the reply runs on the new model.
    assert (job["kind"], job["status"], job["queue_position"]) == ("maintenance", "queued", 1)
    assert client.post(f"{API}/settings/models/apply").json()["id"] == job["id"]

    scheduler = scheduler_for(engine, profiles, orchestrator)
    reloaded = await scheduler.run_once()
    assert reloaded is not None
    assert (reloaded.id, reloaded.status) == (job["id"], JobStatus.COMPLETED)
    assert reloaded.result == {"profile_id": "gemma-4-12b", "model_file": small.file}
    # The runtime stopped, the release was verified (IDLE), and only then the new file loaded.
    assert orchestrator.requests == [("none", job["id"]), ("llm", job["id"])]
    assert orchestrator.loaded == [small.file]

    answered = await scheduler.run_once()
    assert answered is not None
    assert (answered.id, answered.status) == (reply.id, JobStatus.COMPLETED)
    assert orchestrator.leased == [(reply.id, [small.file])]


@pytest.mark.anyio
async def test_a_profile_above_the_tier_or_with_a_missing_file_is_refused_before_any_mode_change(
    engine: Engine, settings: Settings, catalog: Catalog, models: Path, launch_file: Path
) -> None:
    place(models, catalog, "gemma-4-26b-a4b", "gemma-4-12b")
    profiles = profiles_for(engine, catalog, models, launch_file, "reduced")
    profiles.ensure_launch_file()
    before = launch_file.read_bytes()
    assert values(launch_file)["MODEL_PROFILE"] == "gemma-4-12b"
    orchestrator = Orchestrator(launch_file)
    client = client_for(settings, engine, profiles)

    listed = {item["id"]: item for item in client.get(f"{API}/settings/models").json()["text"]}
    assert (listed["gemma-4-26b-a4b"]["state"], listed["gemma-4-26b-a4b"]["selectable"]) == ("verified", False)

    for profile_id, needle in (
        ("gemma-4-26b-a4b", "20 GiB"),
        ("rocinante-x-12b", ".\\scripts\\lyrveil.cmd models -Model rocinante-x-12b"),
        ("not-a-profile", "non esiste"),
    ):
        refused = client.patch(f"{API}/settings/models", json={"llm_profile_id": profile_id})
        assert (refused.status_code, refused.json()["code"]) == (409, "MODEL_PROFILE_UNAVAILABLE")
        assert needle in refused.json()["detail"]
    assert launch_file.read_bytes() == before
    assert read(engine, lambda connection: get_value(connection, LLM_PROFILE_ID)) is None
    assert client.patch(f"{API}/settings/models", json={"llm_profile_id": "../x"}).status_code == 422

    # A model file that went missing refuses Applica ora before a job exists.
    small = next(profile for profile in catalog.text if profile.id == "gemma-4-12b")
    (models / "llm" / small.file).unlink()
    refused = client.post(f"{API}/settings/models/apply")
    assert (refused.status_code, refused.json()["code"]) == (409, "MODEL_PROFILE_UNAVAILABLE")
    assert depth(engine) == 0

    # A file that goes missing after the job was queued fails the job before the mode changes.
    place(models, catalog, "gemma-4-12b")
    assert client.post(f"{API}/settings/models/apply").status_code == 202
    (models / "llm" / small.file).write_bytes(b"\0" * (small.bytes + 1))
    failed = await scheduler_for(engine, profiles, orchestrator).run_once()
    assert failed is not None
    assert (failed.status, failed.error_code) == (JobStatus.FAILED, MODEL_FILE_MISSING)
    assert orchestrator.requests == []
    assert launch_file.read_bytes() == before


def test_custom_registration_accepts_only_file_names_of_the_two_folders_with_the_two_extensions(
    engine: Engine, settings: Settings, catalog: Catalog, models: Path, launch_file: Path, tmp_path: Path
) -> None:
    place(models, catalog, "gemma-4-12b")
    profiles = profiles_for(engine, catalog, models, launch_file, "reduced")
    client = client_for(settings, engine, profiles)
    llm = models / "llm"
    (llm / CUSTOM_TEXT).write_bytes(b"text model")
    (models / "image" / "checkpoints" / CUSTOM_IMAGE).write_bytes(b"image model")
    for name in ("model.ckpt", "model.pt", "model.bin"):
        (llm / name).write_bytes(b"pickle")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.gguf").write_bytes(b"outside the volume")
    os.symlink(outside / "secret.gguf", llm / "linked.gguf")
    os.symlink(llm / CUSTOM_TEXT, llm / "alias.gguf")
    (llm / "folder.gguf").mkdir()

    def register(kind: str, name: str, template: str | None = "gemma4") -> tuple[int, str]:
        body = {"kind": kind, "file": name} | ({} if template is None else {"template": template})
        answer = client.post(f"{API}/settings/models/custom", json=body)
        return answer.status_code, answer.json().get("code", "")

    for name in (
        "../outside/secret.gguf",
        "..\\outside\\secret.gguf",
        "/models/llm/" + CUSTOM_TEXT,
        "llm/" + CUSTOM_TEXT,
        "model.ckpt",
        "model.pt",
        "model.bin",
        CUSTOM_IMAGE,
        "linked.gguf",
        "alias.gguf",
        "folder.gguf",
        "my model.gguf",
    ):
        assert register("text", name) == (422, "VALIDATION_ERROR"), name
    assert register("image", CUSTOM_TEXT, None) == (422, "VALIDATION_ERROR")
    assert register("image", "../../llm/" + CUSTOM_TEXT, None) == (422, "VALIDATION_ERROR")
    assert register("text", CUSTOM_TEXT, None) == (422, "VALIDATION_ERROR")
    assert register("image", CUSTOM_IMAGE, "gemma4") == (422, "VALIDATION_ERROR")
    assert register("text", "absent.gguf") == (404, "NOT_FOUND")
    small = next(profile for profile in catalog.text if profile.id == "gemma-4-12b")
    assert register("text", small.file) == (409, "CONFLICT")
    assert depth(engine) == 0

    # The listing offers only the two unregistered files that registration accepts.
    local = client.get(f"{API}/settings/models").json()["local_files"]
    assert local == [
        {"kind": "text", "file": CUSTOM_TEXT, "bytes": 10},
        {"kind": "image", "file": CUSTOM_IMAGE, "bytes": 11},
    ]

    # A model folder that is itself a link leads outside the volume, so its files are refused too.
    linked_root = tmp_path / "linked-models"
    linked_root.mkdir()
    os.symlink(outside, linked_root / "llm")
    with pytest.raises(ModelFileRefusedError):
        ModelFiles(linked_root).resolve("text", "secret.gguf")


@pytest.mark.anyio
async def test_the_hash_job_stores_the_custom_profile_which_can_then_be_selected_and_removed(
    engine: Engine, settings: Settings, catalog: Catalog, models: Path, launch_file: Path
) -> None:
    place(models, catalog, "gemma-4-12b")
    profiles = profiles_for(engine, catalog, models, launch_file, "reduced")
    profiles.ensure_launch_file()
    client = client_for(settings, engine, profiles)
    text_bytes, image_bytes = b"custom text model" * 100, b"custom checkpoint" * 50
    (models / "llm" / CUSTOM_TEXT).write_bytes(text_bytes)
    (models / "image" / "checkpoints" / CUSTOM_IMAGE).write_bytes(image_bytes)
    text_id = "custom-" + hashlib.sha256(text_bytes).hexdigest()[:12]
    image_id = "custom-" + hashlib.sha256(image_bytes).hexdigest()[:12]

    text_body = {"kind": "text", "file": CUSTOM_TEXT, "template": "chatml"}
    first = client.post(f"{API}/settings/models/custom", json=text_body)
    assert first.status_code == 202, first.text
    assert client.post(f"{API}/settings/models/custom", json=text_body).json()["id"] == first.json()["id"]
    scheduler = scheduler_for(engine, profiles, Orchestrator(launch_file))
    hashed_text = await scheduler.run_once()
    image = client.post(f"{API}/settings/models/custom", json={"kind": "image", "file": CUSTOM_IMAGE})
    assert image.status_code == 202, image.text
    hashed_image = await scheduler.run_once()

    for job, profile_id, kind, content in (
        (hashed_text, text_id, "text", text_bytes),
        (hashed_image, image_id, "image", image_bytes),
    ):
        assert job is not None
        digest = hashlib.sha256(content).hexdigest()
        expected = {"profile_id": profile_id, "kind": kind, "bytes": len(content), "sha256": digest}
        assert (job.status, job.result) == (JobStatus.COMPLETED, expected)
    registered = read(engine, lambda connection: get_value(connection, MODELS_CUSTOM))
    assert isinstance(registered, list)
    assert [(item["id"], item["kind"], item["file"], item["bytes"], item["template"]) for item in registered] == [
        (text_id, "text", CUSTOM_TEXT, len(text_bytes), "chatml"),
        (image_id, "image", CUSTOM_IMAGE, len(image_bytes), None),
    ]

    listed = client.get(f"{API}/settings/models").json()
    assert listed["local_files"] == []
    custom = next(item for item in listed["text"] if item["id"] == text_id)
    assert (custom["custom"], custom["state"], custom["selectable"], custom["template"]) == (
        True,
        "verified",
        True,
        "chatml",
    )
    assert client.post(f"{API}/settings/models/custom", json=text_body).status_code == 409

    # The custom profile takes the launch values of the reduced-tier catalog profile of its family, levers off.
    assert client.patch(f"{API}/settings/models", json={"llm_profile_id": text_id}).status_code == 200
    family = next(profile for profile in catalog.text if profile.template == "chatml")
    launch = values(launch_file)
    assert (launch["MODEL_PROFILE"], launch["MODEL_FILE"], launch["CONTEXT_SIZE"], launch["LEVEL"]) == (
        text_id,
        CUSTOM_TEXT,
        str(family.context_size),
        "A",
    )
    assert (launch["USE_MMAP"], launch["SWA_PADDING"], launch["SMART_CACHE"]) == ("0", "0", "0")
    busy = client.delete(f"{API}/settings/models/custom/{text_id}")
    assert (busy.status_code, busy.json()["code"]) == (409, "CONFLICT")

    assert client.patch(f"{API}/settings/models", json={"llm_profile_id": "gemma-4-12b"}).status_code == 200
    assert client.delete(f"{API}/settings/models/custom/{text_id}").status_code == 204
    assert client.delete(f"{API}/settings/models/custom/{text_id}").status_code == 404
    assert (models / "llm" / CUSTOM_TEXT).read_bytes() == text_bytes
    # A size change makes the remaining profile invalid until it is registered again.
    (models / "image" / "checkpoints" / CUSTOM_IMAGE).write_bytes(image_bytes + b"more")
    images = client.get(f"{API}/settings/models").json()["image"]
    assert next(item for item in images if item["id"] == image_id)["state"] == "size_mismatch"


@pytest.mark.anyio
async def test_the_hash_job_refuses_a_file_replaced_by_a_link_and_stores_nothing(
    engine: Engine, settings: Settings, catalog: Catalog, models: Path, launch_file: Path, tmp_path: Path
) -> None:
    profiles = profiles_for(engine, catalog, models, launch_file, "reduced")
    client = client_for(settings, engine, profiles)
    path = models / "llm" / CUSTOM_TEXT
    path.write_bytes(b"first")
    body = {"kind": "text", "file": CUSTOM_TEXT, "template": "gemma4"}
    assert client.post(f"{API}/settings/models/custom", json=body).status_code == 202
    (tmp_path / "elsewhere.gguf").write_bytes(b"elsewhere")
    path.unlink()
    os.symlink(tmp_path / "elsewhere.gguf", path)

    failed = await scheduler_for(engine, profiles, Orchestrator(launch_file)).run_once()
    assert failed is not None
    assert (failed.status, failed.error_code) == (JobStatus.FAILED, MODEL_FILE_MISSING)
    assert read(engine, lambda connection: get_value(connection, MODELS_CUSTOM)) is None


def test_the_tier_follows_the_memory_docker_sees(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       24608388 kB\nMemFree:        1000 kB\n", encoding="ascii")
    assert machine_tier(meminfo) == "target"
    meminfo.write_text("MemTotal:       16000000 kB\n", encoding="ascii")
    assert machine_tier(meminfo) == "reduced"
    assert machine_tier(tmp_path / "missing") == "reduced"
