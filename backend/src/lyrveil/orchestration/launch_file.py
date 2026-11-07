"""Writes and reads the whole launch file of the text runtime, runtime/llm-launch.env, for the active text profile.

docs/configuration.md (Launch files) lists the keys and their allowed values; docs/models.md (Selecting a profile) says
which ones a profile sets. Every key is written, in the order of the example file config/examples/llm-launch.env, at
level A, so a profile change is also the user's way back to level A after a lowering (LaunchLevels). The text runtime
reads the file only when it starts: a new file applies at its next load.

A whole file replaces the old one in one rename, so the runtime never reads half a file. A lowering by LaunchLevels that
runs at the same moment can be lost; the new profile then starts at level A, which is what a profile change asks for.
"""

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from lyrveil.catalog import TextProfile

# The keys of docs/configuration.md, in the order the example file and this module write them.
LAUNCH_KEYS = (
    "MODEL_PROFILE",
    "MODEL_FILE",
    "CONTEXT_SIZE",
    "GPU_LAYERS",
    "BATCH_SIZE",
    "MOE_CPU_LAYERS",
    "USE_MMAP",
    "KV_QUANT",
    "SWA_PADDING",
    "SMART_CACHE",
    "LEVEL",
)
# The permissions of a new file: the backend writes it, and the text runtime, another container, reads it.
NEW_FILE_MODE = 0o644
_LINE = re.compile(r"^(?P<key>[A-Z_]+)=(?P<value>.*)$")


@dataclass(frozen=True)
class LaunchValues:
    """The values of one launch file. `profile_id` and `model_file` were checked by the caller: a catalog profile or a
    registered custom profile, and a file name of the text model folder."""

    profile_id: str
    model_file: str
    context_size: int
    batch_size: int
    use_mmap: bool
    kv_quant: str
    swa_padding: int
    smart_cache: int
    moe_cpu_layers: int = 0
    gpu_layers: int = -1
    level: str = "A"

    def lines(self) -> list[str]:
        """Returns the KEY=VALUE lines of the file, one per key of LAUNCH_KEYS, in that order."""
        values = {
            "MODEL_PROFILE": self.profile_id,
            "MODEL_FILE": self.model_file,
            "CONTEXT_SIZE": str(self.context_size),
            "GPU_LAYERS": str(self.gpu_layers),
            "BATCH_SIZE": str(self.batch_size),
            "MOE_CPU_LAYERS": str(self.moe_cpu_layers),
            "USE_MMAP": "1" if self.use_mmap else "0",
            "KV_QUANT": self.kv_quant,
            "SWA_PADDING": str(self.swa_padding),
            "SMART_CACHE": str(self.smart_cache),
            "LEVEL": self.level,
        }
        return [f"{key}={values[key]}" for key in LAUNCH_KEYS]


def catalog_values(
    profile: TextProfile, *, profile_id: str | None = None, model_file: str | None = None
) -> LaunchValues:
    """Returns the level-A values of `profile`. With `profile_id` and `model_file`, the values are those of a custom
    profile that borrows `profile`'s context, batch and cache type, with every lever of docs/models.md off."""
    if profile_id is None:
        return LaunchValues(
            profile_id=profile.id,
            model_file=profile.file,
            context_size=profile.context_size,
            batch_size=profile.batch_size,
            use_mmap=profile.use_mmap,
            kv_quant=profile.kv_quant,
            swa_padding=profile.swa_padding,
            smart_cache=profile.smart_cache,
        )
    if model_file is None:
        raise ValueError("a custom profile needs its model file")
    return LaunchValues(
        profile_id=profile_id,
        model_file=model_file,
        context_size=profile.context_size,
        batch_size=profile.batch_size,
        use_mmap=False,
        kv_quant=profile.kv_quant,
        swa_padding=0,
        smart_cache=0,
    )


def write_launch_file(path: Path, values: LaunchValues) -> None:
    """Replaces the file at `path` with `values`, creating its folder when needed. A file that exists keeps its
    permissions; a new one gets NEW_FILE_MODE. Raises OSError when it cannot be written, leaving the old file as it
    was and no temporary file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = NEW_FILE_MODE
    temporary = path.with_name(f".{path.name}.tmp")
    header = f"# Written by the backend for the text profile {values.profile_id} (docs/configuration.md)."
    try:
        temporary.write_text("\n".join([header, *values.lines()]) + "\n", encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def read_launch_settings(path: Path) -> dict[str, str] | None:
    """Returns the KEY=VALUE lines of the file whose key is in LAUNCH_KEYS, or None when the file is missing or
    unreadable. Comments and other lines are skipped; a repeated key keeps its last value."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    settings: dict[str, str] = {}
    for line in lines:
        match = _LINE.match(line)
        if match is not None and match["key"] in LAUNCH_KEYS:
            settings[match["key"]] = match["value"]
    return settings
