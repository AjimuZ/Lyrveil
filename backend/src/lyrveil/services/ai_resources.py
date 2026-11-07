"""AiResources: the snapshot behind GET /system/resources, the AI resources screen (docs/frontend.md, screen 10).

It joins four sources, each read when asked and never cached: the GPU state and the card's memory from the control API,
the active jobs from `generation_jobs`, and the active text model with its launch level from the launch file. A source
that cannot be read leaves its part `None`, so the screen shows it as unknown instead of a made-up value. The snapshot
holds no user text: only enums, counts, MiB and profile names.

Every method blocks on the network, the database or a file: call it from a worker thread.
"""

from typing import Any, Protocol

from sqlalchemy import Connection, Engine, text

from lyrveil.jobs.scheduler import read
from lyrveil.orchestration.client import GpuStatus, OrchestratorUnavailableError, VramReading
from lyrveil.orchestration.gpu_state import label_key
from lyrveil.services.model_profiles import ModelProfiles

_MODES = ("llm", "image", "none")


class ResourceSource(Protocol):
    """The two reads of OrchestratorClient the snapshot uses."""

    def read_state(self) -> GpuStatus: ...
    def read_vram(self) -> VramReading | None: ...


class AiResources:
    """Builds the snapshot over the orchestrator `source`, the database `engine` and, when the catalog loaded,
    `profiles` for the active model."""

    def __init__(self, engine: Engine, source: ResourceSource, profiles: ModelProfiles | None) -> None:
        self._engine = engine
        self._source = source
        self._profiles = profiles

    def snapshot(self) -> dict[str, Any]:
        """Returns `gpu`, `vram`, `queue` and `model`.

        - `gpu`: `state`, `label_key` (the it.json key of the interface label, or None), `desired_mode`, `draining`,
          `blocked_reason` and `blocked_vram` (`mode`, `free_mib`, `required_mib`); None when the control API did not
          answer.
        - `vram`: `device_name`, `total_mib`, `used_mib`, `free_mib` of the whole card; None when it could not be read.
        - `queue`: `queued` and `running` job counts, in total and for each required mode `llm`, `image` and `none`.
        - `model`: `profile_id`, `display_name`, `level` (A, B, C or CUSTOM) and `context_size` of the launch file's
          text model; None when there is no launch file. `level` and `context_size` are None when the file lacks them.
        """
        return {
            "gpu": self._gpu(),
            "vram": self._vram(),
            "queue": read(self._engine, self._queue),
            "model": self._model(),
        }

    def _gpu(self) -> dict[str, Any] | None:
        try:
            status = self._source.read_state()
        except OrchestratorUnavailableError:
            return None
        blocked = status.blocked_vram
        return {
            "state": status.state,
            "label_key": label_key(status),
            "desired_mode": status.desired_mode,
            "draining": status.draining,
            "blocked_reason": status.blocked_reason,
            "blocked_vram": None
            if blocked is None
            else {"mode": blocked.mode, "free_mib": blocked.free_mib, "required_mib": blocked.required_mib},
        }

    def _vram(self) -> dict[str, Any] | None:
        try:
            reading = self._source.read_vram()
        except OrchestratorUnavailableError:
            return None
        if reading is None:
            return None
        return {
            "device_name": reading.device_name,
            "total_mib": reading.total_mib,
            "used_mib": reading.used_mib,
            "free_mib": reading.free_mib,
        }

    @staticmethod
    def _queue(connection: Connection) -> dict[str, Any]:
        rows = connection.execute(
            text(
                "SELECT required_mode, status, count(*) AS jobs FROM generation_jobs "
                "WHERE status IN ('queued', 'running') GROUP BY required_mode, status"
            )
        ).all()
        by_mode = {mode: {"queued": 0, "running": 0} for mode in _MODES}
        for mode, status, jobs in rows:
            if mode in by_mode:
                by_mode[mode][status] += jobs
        return {
            "queued": sum(item["queued"] for item in by_mode.values()),
            "running": sum(item["running"] for item in by_mode.values()),
            "by_mode": by_mode,
        }

    def _model(self) -> dict[str, Any] | None:
        if self._profiles is None:
            return None
        launch = self._profiles.launch_settings()
        if launch is None:
            return None
        profile_id = launch.get("MODEL_PROFILE", "")
        context = launch.get("CONTEXT_SIZE", "")
        return {
            "profile_id": profile_id,
            "display_name": self._profiles.text_name(profile_id) or profile_id,
            "level": launch.get("LEVEL"),
            "context_size": int(context) if context.isdigit() else None,
        }
