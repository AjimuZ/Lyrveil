"""GET /system/resources (docs/api.md, System): the snapshot of the AI resources screen, on a migrated database and a
launch file in a temporary folder, over a fake control API."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from integration.test_launch_levels import find_catalog
from lyrveil.catalog import parse_catalog
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import write
from lyrveil.main import create_app
from lyrveil.model_files import ModelFiles
from lyrveil.orchestration.client import (
    BlockedVram,
    GpuStatus,
    OrchestratorUnavailableError,
    VramReading,
)
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.ai_resources import AiResources
from lyrveil.services.model_profiles import ModelProfiles
from lyrveil.settings import Settings

API = "/api/v1"


class Source:
    """The two reads of the control API; `down` makes both fail as an unreachable orchestrator does."""

    def __init__(self, status: GpuStatus, vram: VramReading | None) -> None:
        self.status = status
        self.vram = vram
        self.down = False

    def read_state(self) -> GpuStatus:
        if self.down:
            raise OrchestratorUnavailableError("down")
        return self.status

    def read_vram(self) -> VramReading | None:
        if self.down:
            raise OrchestratorUnavailableError("down")
        return self.vram


def client_for(settings: Settings, engine: Engine, tmp_path: Path, source: Source) -> TestClient:
    upgrade_to_head(engine)
    catalog = parse_catalog(json.loads(find_catalog().read_text(encoding="utf-8")))
    profiles = ModelProfiles(engine, catalog, "reduced", ModelFiles(tmp_path / "models"), tmp_path / "llm-launch.env")
    app = create_app(settings, engine, model_profiles=profiles, ai_resources=AiResources(engine, source, profiles))
    return TestClient(app, base_url="http://localhost:4200")


def test_the_snapshot_joins_the_gpu_state_the_card_the_queue_and_the_launch_level(
    engine: Engine, settings: Settings, tmp_path: Path
) -> None:
    status = GpuStatus(
        state="IDLE",
        desired_mode="image",
        draining=False,
        blocked_reason="GPU_INSUFFICIENT_VRAM",
        blocked_vram=BlockedVram(mode="image", free_mib=3000, required_mib=9000),
    )
    source = Source(status, VramReading(device_name="Fake GPU", total_mib=12282, used_mib=1489, free_mib=10793))
    client = client_for(settings, engine, tmp_path, source)
    (tmp_path / "llm-launch.env").write_text(
        "MODEL_PROFILE=gemma-4-12b\nCONTEXT_SIZE=6144\nLEVEL=B\n", encoding="utf-8"
    )
    write(engine, lambda connection: JobQueue().enqueue(connection, JobKind.CHAT_REPLY))
    write(engine, lambda connection: JobQueue().enqueue(connection, JobKind.IMAGE_REQUEST))

    body = client.get(f"{API}/system/resources").json()

    assert body["gpu"] == {
        "state": "IDLE",
        "label_key": "gpu.blocked.insufficientVram",
        "desired_mode": "image",
        "draining": False,
        "blocked_reason": "GPU_INSUFFICIENT_VRAM",
        "blocked_vram": {"mode": "image", "free_mib": 3000, "required_mib": 9000},
    }
    assert body["vram"] == {"device_name": "Fake GPU", "total_mib": 12282, "used_mib": 1489, "free_mib": 10793}
    assert body["queue"]["queued"] == 2
    assert body["queue"]["by_mode"]["llm"] == {"queued": 1, "running": 0}
    assert body["queue"]["by_mode"]["image"] == {"queued": 1, "running": 0}
    assert body["model"] == {
        "profile_id": "gemma-4-12b",
        "display_name": "Gemma 4 12B",
        "level": "B",
        "context_size": 6144,
    }


def test_a_source_that_cannot_be_read_is_null_and_the_route_still_answers(
    engine: Engine, settings: Settings, tmp_path: Path
) -> None:
    source = Source(GpuStatus(state="LLM_READY", desired_mode="llm", draining=False), None)
    source.down = True
    client = client_for(settings, engine, tmp_path, source)

    response = client.get(f"{API}/system/resources")

    assert response.status_code == 200
    body = response.json()
    assert (body["gpu"], body["vram"], body["model"]) == (None, None, None)
    assert body["queue"]["queued"] == 0
