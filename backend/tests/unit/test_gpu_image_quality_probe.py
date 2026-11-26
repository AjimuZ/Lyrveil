"""Tests of tests/gpu_probe/image_quality.py, the backend side of T-QLT-02 and T-QLT-07, without a GPU.

The quality level renders the probe's cells and the owner reviews them blind, so the plan must hold exactly the
renders the protocol names, and each lever must change only what ADR 0036 says it changes.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PROBE_PATH = Path(__file__).resolve().parents[1] / "gpu_probe" / "image_quality.py"


def _load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("image_quality", PROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def _plan(profile: str, mode: str) -> dict[str, Any]:
    document: dict[str, Any] = probe.plan_document({"profile": profile, "mode": mode, "tier": "target"})
    return document


def _cells(document: dict[str, Any], section: str) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = document["cells"]
    return [cell for cell in cells if cell["section"] == section]


def test_the_full_grid_of_a_profile_is_three_characters_two_scenes_three_seeds_and_six_settings() -> None:
    document = _plan("noobai-xl-1.1", "full")

    grid = _cells(document, "grid")
    assert document["is_default"] is False
    assert len(grid) == 3 * 2 * 3 * 6
    assert {cell["variant"] for cell in grid} == {
        "default",
        "clip-skip-1",
        "cfg-4.5",
        "cfg-7",
        "denoise-0.35",
        "denoise-0.55",
    }
    assert _cells(document, "identity") == []
    assert _cells(document, "pag") == []


def test_the_default_profile_adds_the_sizes_the_identity_protocol_and_the_levers() -> None:
    document = _plan("illustrious-xl-v2.0", "full")

    assert document["is_default"] is True
    assert len(_cells(document, "grid")) == 3 * 2 * 3 * 9
    identity = _cells(document, "identity")
    assert len(identity) == 5 * 4 * 3 * 2
    assert {cell["variant"] for cell in identity} == {"baseline", "identity-weight-1.1"}
    pag = _cells(document, "pag")
    assert len(pag) == 5 * 4 * 2
    assert {cell["variant"] for cell in pag} == {"pag-1.5", "pag-3"}
    assert {cell["character"] for cell in identity} == {"Nerea", "Ilario", "Tessa", "Ottavia", "Bruno"}
    ids = [cell["id"] for cell in document["cells"]]
    assert len(ids) == len(set(ids))


def test_the_sizes_use_the_documented_final_sizes() -> None:
    grid = _cells(_plan("illustrious-xl-v2.0", "short"), "grid")

    sizes = {cell["variant"]: (cell["width"], cell["height"]) for cell in grid}
    assert sizes["default"] == (1248, 1824)
    assert sizes["landscape"] == (1824, 1248)
    assert sizes["tall"] == (1152, 2016)
    assert sizes["wide"] == (2016, 1152)


def test_pag_goes_between_the_checkpoint_and_the_first_pass_only() -> None:
    document = _plan("illustrious-xl-v2.0", "short")

    baseline = json.loads(_cells(document, "identity")[0]["workflow"])
    lever = json.loads(_cells(document, "pag")[0]["workflow"])
    assert "20" not in baseline
    assert lever["20"] == {"class_type": "PerturbedAttentionGuidance", "inputs": {"model": ["1", 0], "scale": 1.5}}
    assert lever["6"]["inputs"]["model"] == ["20", 0]
    assert lever["11"]["inputs"]["model"] == ["1", 0]
    lever["6"]["inputs"]["model"] = ["1", 0]
    del lever["20"]
    assert lever == baseline


def test_the_identity_weight_wraps_only_the_hair_and_eye_colour_tags() -> None:
    document = _plan("illustrious-xl-v2.0", "short")

    identity = {cell["variant"]: cell for cell in _cells(document, "identity")}
    plain = json.loads(identity["baseline"]["workflow"])["3"]["inputs"]["text"]
    weighted = json.loads(identity["identity-weight-1.1"]["workflow"])["3"]["inputs"]["text"]
    assert "(red hair:1.1)" in weighted
    assert "(green eyes:1.1)" in weighted
    assert weighted.replace("(red hair:1.1)", "red hair").replace("(green eyes:1.1)", "green eyes") == plain


def test_an_identity_cell_names_the_attributes_the_owner_checks() -> None:
    document = _plan("illustrious-xl-v2.0", "short")

    expected = _cells(document, "identity")[0]["expected"]
    assert expected == {
        "hair_color": "red hair",
        "hair_length": "short hair",
        "eye_color": "green eyes",
        "age": 27,
        "age_band": "woman",
        "outfit": "canvas jacket, many pockets",
    }


def test_the_short_mode_has_one_cell_of_each_kind() -> None:
    document = _plan("illustrious-xl-v2.0", "short")

    assert len(_cells(document, "grid")) == 9
    assert len(_cells(document, "identity")) == 2
    assert len(_cells(document, "pag")) == 2


def test_the_ages_of_the_synthetic_characters_give_the_older_bands() -> None:
    ages = {name: age for name, age, _ in probe.characters()}

    assert ages["Ottavia"] == 52
    assert ages["Bruno"] == 70


def test_a_request_can_ask_for_one_section() -> None:
    document = probe.plan_document(
        {"profile": "illustrious-xl-v2.0", "mode": "short", "tier": "target", "section": "pag"}
    )

    assert {cell["section"] for cell in document["cells"]} == {"pag"}
    with pytest.raises(ValueError, match="unknown section"):
        probe.plan_document({"profile": "illustrious-xl-v2.0", "mode": "short", "tier": "target", "section": "x"})


def test_a_wrong_mode_or_profile_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown mode"):
        probe.plan_document({"profile": "illustrious-xl-v2.0", "mode": "huge", "tier": "target"})
    with pytest.raises(KeyError):
        probe.plan_document({"profile": "missing", "mode": "short", "tier": "target"})
