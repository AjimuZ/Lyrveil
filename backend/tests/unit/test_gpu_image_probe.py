"""Tests of tests/gpu_probe/image_presets.py, the backend side of T-GPU-06 and of the real-GPU part of T-IMG-14,
without a GPU.

The gpu level renders the probe's graphs and records the preset rule the probe computes, so the probe must give the
graph the `render` stage builds for a catalog profile and apply the rule of ADR 0026 exactly at its threshold.
"""

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

from lyrveil.images.workflows import WorkflowSpec, build_workflow

PROBE_PATH = Path(__file__).resolve().parents[1] / "gpu_probe" / "image_presets.py"


def _load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("image_presets", PROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def test_the_alta_graph_is_what_the_render_stage_builds_for_the_profile() -> None:
    document = probe.workflow_document(
        {"profile": "noobai-xl-1.1", "quality": "alta", "aspect": "square", "seeds": [7, 8]}
    )

    graphs = [json.loads(text) for text in document["workflows"]]
    expected = build_workflow(
        WorkflowSpec(
            preset="alta",
            aspect="square",
            ckpt_name="NoobAI-XL-v1.1.safetensors",
            positive=graphs[0]["3"]["inputs"]["text"],
            negative=graphs[0]["4"]["inputs"]["text"],
            seed=7,
        )
    )
    assert graphs[0] == expected
    assert graphs[1]["6"]["inputs"]["seed"] == graphs[1]["11"]["inputs"]["seed"] == 8
    assert (document["template"], document["prompt_preset"]) == ("sdxl_hires.v1.json", "noobai")
    assert (document["width"], document["height"], document["refine_steps"]) == (1536, 1536, 14)
    assert graphs[0]["3"]["inputs"]["text"].startswith("masterpiece, best quality, newest")
    assert "underage" in graphs[0]["4"]["inputs"]["text"]


def test_the_rapida_graph_has_one_pass_and_the_profile_checkpoint() -> None:
    document = probe.workflow_document(
        {"profile": "illustrious-xl-v1.0", "quality": "rapida", "aspect": "square", "seeds": [1]}
    )

    graph = json.loads(document["workflows"][0])
    assert graph["1"]["inputs"]["ckpt_name"] == "Illustrious-XL-v1.0.safetensors"
    assert "11" not in graph
    assert (document["width"], document["height"], document["refine_steps"]) == (1024, 1024, 0)
    assert document["workflows"][0].isascii()


def test_an_unknown_profile_is_refused() -> None:
    with pytest.raises(KeyError):
        probe.workflow_document({"profile": "missing", "quality": "rapida", "aspect": "square", "seeds": [1]})


@pytest.mark.parametrize(
    ("values", "percentile", "expected"),
    [([3.0, 1.0, 2.0], 95, 3.0), ([float(n) for n in range(1, 21)], 95, 19.0), ([5.0], 50, 5.0)],
)
def test_the_nearest_rank_percentile(values: list[float], percentile: float, expected: float) -> None:
    assert probe.nearest_rank(values, percentile) == expected


def test_the_percentile_of_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="no values"):
        probe.nearest_rank([], 95)


@pytest.mark.parametrize(("slowest", "preset"), [(45.0, "alta"), (45.01, "rapida")])
def test_alta_stays_the_default_up_to_45_seconds_at_the_95th_percentile(slowest: float, preset: str) -> None:
    document = probe.rule_document(
        {"tier": "target", "profiles": [{"profile": "illustrious-xl-v2.0", "alta_seconds": [30.0, slowest, 31.0]}]}
    )

    assert document["default_profile"] == "illustrious-xl-v2.0"
    assert document["default_preset"] == preset
    assert document["profiles"][0]["alta_p95_s"] == slowest


def test_the_default_is_undecided_without_alta_times_of_the_default_profile() -> None:
    document = probe.rule_document(
        {
            "tier": "target",
            "profiles": [
                {"profile": "noobai-xl-1.1", "alta_seconds": [20.0], "rapida_seconds": [8.0]},
                {"profile": "illustrious-xl-v2.0", "alta_seconds": [], "rapida_seconds": [8.0]},
            ],
        }
    )

    assert document["default_preset"] == "undecided"
    assert [verdict["preset"] for verdict in document["profiles"]] == ["alta", "undecided"]
    assert document["profiles"][1]["rapida_p95_s"] == 8.0


def test_main_prints_one_ascii_json_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    request = {"tier": "target", "profiles": [{"profile": "illustrious-xl-v2.0", "alta_seconds": [25.0]}]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))

    assert probe.main(["rule"]) == 0
    printed = capsys.readouterr().out
    assert printed.isascii()
    assert json.loads(printed)["default_preset"] == "alta"
    assert probe.main(["generate"]) == 2
