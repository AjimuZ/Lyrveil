"""Tests LaunchLevels, the launch levels of docs/gpu-orchestration.md (Load failures and launch levels), on the shipped
catalog and a launch file in a temporary folder: the values of each level, the lowering from A to C and no further,
the files it leaves alone, and the once-per-failure answer to the orchestrator's classified load failures."""

import stat
from pathlib import Path

import pytest

from lyrveil.catalog import Catalog, load_catalog
from lyrveil.orchestration.client import GpuStatus, LeaseMode, LoadFailure
from lyrveil.orchestration.launch_levels import LaunchLevel, LaunchLevels, LevelValues, level_values

BOOT = "0192f3a0-0000-7000-8000-000000000000"


def find_catalog() -> Path:
    """Returns the shipped catalog in the repository or, in the test image, in /app/config."""
    here = Path(__file__).resolve()
    for root in (here.parents[3], here.parents[2]):
        candidate = root / "config" / "model-catalog.json"
        if candidate.is_file():
            return candidate
    raise AssertionError("model-catalog.json is in neither candidate config folder")


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(find_catalog())


def launch_file(profile: str, level: str = "A", context: int = 16384) -> str:
    """A launch file as setup writes it, with a comment the lowering must keep."""
    return (
        "# The launch file of the text runtime.\n"
        f"MODEL_PROFILE={profile}\n"
        "MODEL_FILE=model.gguf\n"
        f"CONTEXT_SIZE={context}\n"
        "GPU_LAYERS=-1\n"
        "BATCH_SIZE=512\n"
        "MOE_CPU_LAYERS=0\n"
        "USE_MMAP=0\n"
        "KV_QUANT=f16\n"
        "SWA_PADDING=0\n"
        "SMART_CACHE=0\n"
        f"LEVEL={level}\n"
    )


def keys(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


def oom_load(seq: int, *, mode: LeaseMode = "llm", oom: bool = True) -> GpuStatus:
    """The state just after a failed load of `mode`, recorded as event `seq`."""
    failure = LoadFailure(seq=seq, mode=mode, code="LLM_LOAD_FAILED", oom=oom, failures=1, final=False, job_id=None)
    return GpuStatus(
        state="RECOVERING", desired_mode="llm", draining=False, seq=seq, boot_id=BOOT, load_failure=failure
    )


@pytest.mark.parametrize(
    ("profile_id", "expected"),
    [
        ("gemma-4-26b-a4b", [LevelValues(16384, 512), LevelValues(12288, 512), LevelValues(8192, 256)]),
        ("rocinante-x-12b", [LevelValues(8192, 512), LevelValues(6144, 512), LevelValues(4096, 256)]),
    ],
)
def test_each_level_derives_from_the_profiles_context_as_the_table_says(
    catalog: Catalog, profile_id: str, expected: list[LevelValues]
) -> None:
    profile = next(profile for profile in catalog.text if profile.id == profile_id)
    levels = [LaunchLevel.A, LaunchLevel.B, LaunchLevel.C]
    assert [level_values(profile, level) for level in levels] == expected
    assert all(values.gpu_layers == -1 for values in expected)


def test_a_grown_context_rounds_each_level_down_to_an_allowed_size(catalog: Catalog) -> None:
    grown = catalog.text[0].model_copy(update={"context_size": 24576})
    # Three quarters of 24576 is 18432, which the entrypoint refuses: the level takes 16384.
    assert [level_values(grown, level).context_size for level in (LaunchLevel.A, LaunchLevel.B, LaunchLevel.C)] == [
        24576,
        16384,
        12288,
    ]
    with pytest.raises(ValueError, match="CUSTOM"):
        level_values(grown, LaunchLevel.CUSTOM)


def test_lowering_goes_from_a_to_c_and_no_further_keeping_every_other_line(catalog: Catalog, tmp_path: Path) -> None:
    path = tmp_path / "llm-launch.env"
    path.write_text(launch_file("gemma-4-26b-a4b"), encoding="utf-8")
    path.chmod(0o644)
    levels = LaunchLevels(catalog, path)

    assert levels.current() is LaunchLevel.A
    assert levels.lower() is LaunchLevel.B
    assert (keys(path)["LEVEL"], keys(path)["CONTEXT_SIZE"], keys(path)["BATCH_SIZE"]) == ("B", "12288", "512")
    assert levels.lower() is LaunchLevel.C
    after_c = path.read_text(encoding="utf-8")
    assert (keys(path)["LEVEL"], keys(path)["CONTEXT_SIZE"], keys(path)["BATCH_SIZE"]) == ("C", "8192", "256")
    assert levels.lower() is None
    assert path.read_text(encoding="utf-8") == after_c

    # Only the level's keys changed; the comment, the order and the other values stayed, and so did the mode.
    original = launch_file("gemma-4-26b-a4b").splitlines()
    lowered = after_c.splitlines()
    changed = {line.split("=", 1)[0] for line, now in zip(original, lowered, strict=True) if line != now}
    assert changed == {"CONTEXT_SIZE", "BATCH_SIZE", "LEVEL"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert sorted(item.name for item in tmp_path.iterdir()) == ["llm-launch.env"]


@pytest.mark.parametrize(
    "content",
    [
        launch_file("gemma-4-26b-a4b", level="CUSTOM"),
        launch_file("custom-0123456789ab"),
        launch_file("gemma-4-26b-a4b").replace("BATCH_SIZE=512\n", ""),
    ],
    ids=["custom level", "profile outside the catalog", "missing key"],
)
def test_a_file_it_cannot_derive_a_level_for_is_left_alone(catalog: Catalog, tmp_path: Path, content: str) -> None:
    path = tmp_path / "llm-launch.env"
    path.write_text(content, encoding="utf-8")
    assert LaunchLevels(catalog, path).lower() is None
    assert path.read_text(encoding="utf-8") == content


def test_a_missing_file_changes_nothing(catalog: Catalog, tmp_path: Path) -> None:
    levels = LaunchLevels(catalog, tmp_path / "llm-launch.env")
    assert levels.current() is None
    assert levels.lower() is None
    assert list(tmp_path.iterdir()) == []


def test_a_classified_load_failure_lowers_the_level_once_even_across_a_restart(
    catalog: Catalog, tmp_path: Path
) -> None:
    path = tmp_path / "llm-launch.env"
    path.write_text(launch_file("rocinante-x-12b", context=8192), encoding="utf-8")
    levels = LaunchLevels(catalog, path)

    # Another state, a failure that is not out of memory and an image failure change nothing.
    assert levels.observe(GpuStatus(state="IDLE", desired_mode="llm", draining=False, seq=3, boot_id=BOOT)) is None
    assert levels.observe(oom_load(4, oom=False)) is None
    assert levels.observe(oom_load(4, mode="image")) is None
    assert keys(path)["LEVEL"] == "A"

    assert levels.observe(oom_load(5)) is LaunchLevel.B
    assert f"# lowered after orchestrator event {BOOT}/5" in path.read_text(encoding="utf-8").splitlines()
    # The same failure, seen again by this process or by a new one after a restart, is answered already.
    assert levels.observe(oom_load(5)) is None
    assert LaunchLevels(catalog, path).observe(oom_load(5)) is None
    assert keys(path)["LEVEL"] == "B"

    # The next failure, at level B, lowers to C, and the file names only that event.
    assert levels.observe(oom_load(9)) is LaunchLevel.C
    markers = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("# lowered")]
    assert markers == [f"# lowered after orchestrator event {BOOT}/9"]
    assert (keys(path)["CONTEXT_SIZE"], keys(path)["BATCH_SIZE"]) == ("4096", "256")
