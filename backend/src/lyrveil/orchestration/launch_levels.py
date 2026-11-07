"""LaunchLevels: lowers the text runtime's launch level in runtime/llm-launch.env after an allocation failure.

docs/gpu-orchestration.md (Load failures and launch levels). Level A runs the active profile's context size, B three
quarters of it and C half, each rounded down to a CONTEXT_SIZE the entrypoint allows; C also halves the batch, to at
most 256. GPU layers stay on autofit (-1) at every level. The text runtime reads the file when it starts, so a new
level applies at the restart that follows the failure: the orchestrator restarts the runtime after a lease released
with `oom`, and retries a failed load after 5 s.

Two failures lower the level: an LLM_OOM during a generation, which JobScheduler reports with `lower` before it
releases the lease, and a failed load the orchestrator classified as out of memory, which GpuStateFollower reports
with `observe`. A level is never lowered below C, and a file at level CUSTOM, the user's own values, is never changed.
The reply budgets follow by themselves: the context builder reads the size the runtime reports.

The file is read and written as KEY=VALUE data. Only CONTEXT_SIZE, BATCH_SIZE, GPU_LAYERS and LEVEL change, every other
line stays as it was, and the new file replaces the old one in one rename with the old one's permissions. A comment
line records the orchestrator event a lowering answered, so a backend restart does not answer the same failure twice.
"""

import os
import re
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from lyrveil.catalog import Catalog, TextProfile
from lyrveil.logs import get_logger
from lyrveil.orchestration.client import GpuStatus

# The data volume's copy of the launch file the text runtime mounts at /run/lyrveil (docs/configuration.md).
DEFAULT_LAUNCH_FILE = Path("/data/runtime/llm-launch.env")
# The CONTEXT_SIZE values the entrypoint of docker/llm accepts, in increasing order.
CONTEXT_SIZES = (4096, 6144, 8192, 12288, 16384, 24576)
# docs/gpu-orchestration.md, the level table: level C runs batch 256.
LEVEL_C_BATCH = 256
AUTOFIT_GPU_LAYERS = -1
# The comment that names the orchestrator event a lowering answered: `<boot_id>/<seq>`.
_ANSWERED = re.compile(r"^# lowered after orchestrator event (?P<event>[A-Za-z0-9-]{1,64}/[0-9]{1,18})$")
_LINE = re.compile(r"^(?P<key>[A-Z_]+)=(?P<value>.*)$")
_EVENT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

logger = get_logger(__name__)


class LaunchLevel(StrEnum):
    """The LEVEL values of the launch file. CUSTOM marks values the user set, which no failure changes."""

    A = "A"
    B = "B"
    C = "C"
    CUSTOM = "CUSTOM"


_NEXT: Mapping[LaunchLevel, LaunchLevel] = {LaunchLevel.A: LaunchLevel.B, LaunchLevel.B: LaunchLevel.C}
# The share of the profile's context size each level runs, as (numerator, denominator).
_CONTEXT_SHARE: Mapping[LaunchLevel, tuple[int, int]] = {
    LaunchLevel.A: (1, 1),
    LaunchLevel.B: (3, 4),
    LaunchLevel.C: (1, 2),
}


@dataclass(frozen=True)
class LevelValues:
    """The launch values a level gives a profile."""

    context_size: int
    batch_size: int
    gpu_layers: int = AUTOFIT_GPU_LAYERS


def level_values(profile: TextProfile, level: LaunchLevel) -> LevelValues:
    """Returns the values of `level` for `profile`: the share of its context size, rounded down to an allowed size and
    never below the smallest, and its batch size, at most LEVEL_C_BATCH at level C. Raises ValueError for CUSTOM,
    whose values are the user's."""
    if level is LaunchLevel.CUSTOM:
        raise ValueError("level CUSTOM has no derived values")
    numerator, denominator = _CONTEXT_SHARE[level]
    target = profile.context_size * numerator // denominator
    context = max((size for size in CONTEXT_SIZES if size <= target), default=CONTEXT_SIZES[0])
    batch = min(profile.batch_size, LEVEL_C_BATCH) if level is LaunchLevel.C else profile.batch_size
    return LevelValues(context_size=context, batch_size=batch)


class LaunchLevels:
    """Lowers the level of the launch file at `path` for the profile it names, with the values `catalog` gives that
    profile. Safe to call from several worker threads: one lowering runs at a time. Every method blocks on the file:
    run it in a worker thread."""

    def __init__(self, catalog: Catalog, path: Path = DEFAULT_LAUNCH_FILE) -> None:
        self._profiles = {profile.id: profile for profile in catalog.text}
        self._path = path
        self._lock = threading.Lock()

    def current(self) -> LaunchLevel | None:
        """Returns the level of the launch file, or None when the file is missing, unreadable or has no valid LEVEL."""
        lines = self._read()
        if lines is None:
            return None
        return _level(_settings(lines))

    def lower(self) -> LaunchLevel | None:
        """Moves the launch file to the next level after an allocation failure and returns the new level.

        Returns None, changing nothing, when there is no lower level (C), the level is CUSTOM, or the file is missing,
        unreadable, lacks a key, names a profile outside the catalog (a custom profile) or cannot be written; each case
        is logged. The caller goes on: the next load then runs the same level.
        """
        with self._lock:
            return self._lower(None)

    def observe(self, status: GpuStatus) -> LaunchLevel | None:
        """Lowers the level for a failed load of the text runtime that the orchestrator classified as out of memory,
        once per failure, and returns the new level as `lower` does.

        Returns None, changing nothing, for any other state, and for a failure the launch file says was already
        answered, by this process or before a restart.
        """
        failure = status.load_failure
        if failure is None or failure.mode != "llm" or not failure.oom:
            return None
        event = (
            f"{status.boot_id}/{failure.seq}"
            if status.boot_id is not None and _EVENT_ID.match(status.boot_id)
            else None
        )
        with self._lock:
            lines = self._read()
            if lines is not None and event is not None and _answered(lines) == event:
                return None
            return self._lower(event)

    def _lower(self, event: str | None) -> LaunchLevel | None:
        lines = self._read()
        if lines is None:
            logger.warning("launch_level_unchanged", error_code="LAUNCH_FILE_UNREADABLE")
            return None
        settings = _settings(lines)
        level = _level(settings)
        following = None if level is None else _NEXT.get(level)
        profile = self._profiles.get(settings.get("MODEL_PROFILE", ""))
        if following is None or profile is None:
            # `state` carries the level, since the log allowlist has no field of its own for it.
            logger.warning("launch_level_unchanged", state=settings.get("LEVEL", "none")[:8])
            return None
        values = level_values(profile, following)
        changes = {
            "CONTEXT_SIZE": str(values.context_size),
            "BATCH_SIZE": str(values.batch_size),
            "GPU_LAYERS": str(values.gpu_layers),
            "LEVEL": following.value,
        }
        if not set(changes) <= set(settings):
            logger.warning("launch_level_unchanged", error_code="LAUNCH_FILE_INCOMPLETE")
            return None
        rewritten = [_replaced(line, changes) for line in lines if _ANSWERED.match(line) is None]
        if event is not None:
            rewritten.append(f"# lowered after orchestrator event {event}")
        try:
            self._write(rewritten)
        except OSError:
            logger.warning("launch_level_unchanged", error_code="LAUNCH_FILE_UNWRITABLE", exc_info=True)
            return None
        logger.info("launch_level_lowered", state=following.value)
        return following

    def _read(self) -> list[str] | None:
        try:
            return self._path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None

    def _write(self, lines: list[str]) -> None:
        """Writes `lines` beside the file, with its permissions, then renames it over the file in one step, so the
        runtime never reads half a file."""
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        mode = stat.S_IMODE(self._path.stat().st_mode)
        try:
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.chmod(temporary, mode)
            os.replace(temporary, self._path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise


def _settings(lines: list[str]) -> dict[str, str]:
    """Returns the KEY=VALUE lines of the file. Comments, blank lines and other lines are skipped."""
    settings: dict[str, str] = {}
    for line in lines:
        match = _LINE.match(line)
        if match is not None:
            settings[match["key"]] = match["value"]
    return settings


def _level(settings: Mapping[str, str]) -> LaunchLevel | None:
    try:
        return LaunchLevel(settings.get("LEVEL", ""))
    except ValueError:
        return None


def _answered(lines: list[str]) -> str | None:
    """Returns the orchestrator event the last lowering answered, or None."""
    for line in lines:
        match = _ANSWERED.match(line)
        if match is not None:
            return match["event"]
    return None


def _replaced(line: str, changes: Mapping[str, str]) -> str:
    match = _LINE.match(line)
    if match is None or match["key"] not in changes:
        return line
    return f"{match['key']}={changes[match['key']]}"
