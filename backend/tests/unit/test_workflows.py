"""Tests the workflow templates and builder (T-IMG-14, unit level): graphs, links, seed, sizes, ckpt_name, tiling."""

import json
from dataclasses import replace
from typing import Any

import pytest

from lyrveil.images.workflows import (
    FIRST_PASS_SIZES,
    TEMPLATE_NAME_PATTERN,
    TEMPLATES,
    WorkflowError,
    WorkflowSpec,
    build_workflow,
    final_size,
    load_template,
    workflows_dir,
)
from lyrveil.runtimes.comfyui import ProgressMap

# docs/image-generation.md (Quality presets): the final sizes, in the order square, portrait, landscape, tall, wide.
ALTA_FINAL = {
    "square": (1536, 1536),
    "portrait": (1248, 1824),
    "landscape": (1824, 1248),
    "tall": (1152, 2016),
    "wide": (2016, 1152),
}
BASE_CLASSES = {
    "1": "CheckpointLoaderSimple",
    "2": "CLIPSetLastLayer",
    "3": "CLIPTextEncode",
    "4": "CLIPTextEncode",
    "5": "EmptyLatentImage",
    "6": "KSampler",
    "8": "SaveImage",
}
TILE = {"tile_size": 512, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}


def spec(**changes: Any) -> WorkflowSpec:
    base = WorkflowSpec(
        preset="alta",
        aspect="portrait",
        ckpt_name="NoobAI-XL-v1.1.safetensors",
        positive="1girl, red hair",
        negative="worst quality",
        seed=1234567,
    )
    return replace(base, **changes)


def classes(graph: dict[str, Any]) -> dict[str, str]:
    return {node_id: node["class_type"] for node_id, node in graph.items()}


def test_every_template_on_disk_is_registered_versioned_and_loads() -> None:
    on_disk = {path.name for path in workflows_dir().glob("*.json")}
    assert on_disk == set(TEMPLATES.values())
    for name in on_disk:
        assert TEMPLATE_NAME_PATTERN.fullmatch(name), name
        graph = load_template(name)
        for node_id, node in graph.items():
            assert set(node) == {"class_type", "inputs"}, (name, node_id)
            for value in node["inputs"].values():
                if isinstance(value, list):
                    assert str(value[0]) in graph, (name, node_id)


def test_rapida_builds_the_one_pass_graph() -> None:
    graph = build_workflow(spec(preset="rapida"))
    assert classes(graph) == {**BASE_CLASSES, "7": "VAEDecode"}
    assert graph["6"]["inputs"]["model"] == ["1", 0]
    assert graph["6"]["inputs"]["positive"] == ["3", 0]
    assert graph["6"]["inputs"]["negative"] == ["4", 0]
    assert graph["6"]["inputs"]["latent_image"] == ["5", 0]
    assert graph["7"]["inputs"] == {"samples": ["6", 0], "vae": ["1", 2]}
    assert graph["8"]["inputs"]["images"] == ["7", 0]


def test_alta_builds_the_two_pass_graph() -> None:
    graph = build_workflow(spec())
    assert classes(graph) == {
        **BASE_CLASSES,
        "7": "VAEDecode",
        "9": "ImageScaleBy",
        "10": "VAEEncode",
        "11": "KSampler",
        "12": "VAEDecode",
    }
    assert graph["9"]["inputs"] == {"image": ["7", 0], "upscale_method": "lanczos", "scale_by": 1.5}
    assert graph["10"]["inputs"] == {"pixels": ["9", 0], "vae": ["1", 2]}
    second = graph["11"]["inputs"]
    assert second["latent_image"] == ["10", 0]
    assert (second["model"], second["positive"], second["negative"]) == (["1", 0], ["3", 0], ["4", 0])
    assert (second["steps"], second["denoise"]) == (14, 0.45)
    assert (second["sampler_name"], second["scheduler"]) == ("dpmpp_2m", "karras")
    assert graph["12"]["inputs"] == {"samples": ["11", 0], "vae": ["1", 2]}
    assert graph["8"]["inputs"]["images"] == ["12", 0]


@pytest.mark.parametrize("tiled", [False, True])
def test_both_passes_share_seed_and_cfg_and_keep_the_profile_sampler(tiled: bool) -> None:
    graph = build_workflow(spec(tiled=tiled, seed=4294967295, cfg=4.5, steps=30, sampler="euler"))
    first, second = graph["6"]["inputs"], graph["11"]["inputs"]
    assert first["seed"] == second["seed"] == 4294967295
    assert first["cfg"] == second["cfg"] == 4.5
    assert (first["steps"], first["sampler_name"], first["scheduler"], first["denoise"]) == (30, "euler", "normal", 1.0)


@pytest.mark.parametrize("aspect", list(FIRST_PASS_SIZES))
def test_sizes_follow_the_documented_buckets(aspect: Any) -> None:
    rapida = build_workflow(spec(preset="rapida", aspect=aspect))
    assert (rapida["5"]["inputs"]["width"], rapida["5"]["inputs"]["height"]) == FIRST_PASS_SIZES[aspect]
    assert final_size("rapida", aspect) == FIRST_PASS_SIZES[aspect]
    alta = build_workflow(spec(aspect=aspect))
    assert (alta["5"]["inputs"]["width"], alta["5"]["inputs"]["height"]) == FIRST_PASS_SIZES[aspect]
    assert final_size("alta", aspect) == ALTA_FINAL[aspect]
    width, height = final_size("alta", aspect)
    assert (width % 8, height % 8) == (0, 0)
    assert (width, height) == tuple(round(side * alta["9"]["inputs"]["scale_by"]) for side in FIRST_PASS_SIZES[aspect])


def test_ckpt_name_comes_from_the_spec_only_and_prompts_stay_data() -> None:
    hostile = '"}, "99": {"class_type": "LoadImage", "inputs": {}}, "x": {"'
    graph = build_workflow(spec(positive=hostile, negative=hostile))
    assert graph["1"]["inputs"]["ckpt_name"] == "NoobAI-XL-v1.1.safetensors"
    assert graph["3"]["inputs"]["text"] == graph["4"]["inputs"]["text"] == hostile
    assert "99" not in graph
    assert "Illustrious-XL-v2.0.safetensors" not in json.dumps(graph)


@pytest.mark.parametrize(
    "name", ["../x.safetensors", "a/b.safetensors", "model.ckpt", ".safetensors", "a b.safetensors"]
)
def test_a_ckpt_name_that_is_not_a_plain_file_name_is_refused(name: str) -> None:
    with pytest.raises(WorkflowError):
        build_workflow(spec(ckpt_name=name))


def test_clip_skip_sets_the_last_layer() -> None:
    assert build_workflow(spec(clip_skip=2))["2"]["inputs"]["stop_at_clip_layer"] == -2
    assert build_workflow(spec(clip_skip=1))["2"]["inputs"]["stop_at_clip_layer"] == -1


def test_tiled_rapida_replaces_only_the_decode() -> None:
    plain, tiled = build_workflow(spec(preset="rapida")), build_workflow(spec(preset="rapida", tiled=True))
    assert classes(tiled) == {**classes(plain), "7": "VAEDecodeTiled"}
    assert tiled["7"]["inputs"] == {"samples": ["6", 0], "vae": ["1", 2], **TILE}
    assert {key: value for key, value in tiled.items() if key != "7"} == {
        key: value for key, value in plain.items() if key != "7"
    }


def test_tiled_alta_replaces_both_decodes_and_the_encode() -> None:
    plain, tiled = build_workflow(spec()), build_workflow(spec(tiled=True))
    assert classes(tiled) == {**classes(plain), "7": "VAEDecodeTiled", "10": "VAEEncodeTiled", "12": "VAEDecodeTiled"}
    assert tiled["7"]["inputs"] == {"samples": ["6", 0], "vae": ["1", 2], **TILE}
    assert tiled["10"]["inputs"] == {"pixels": ["9", 0], "vae": ["1", 2], **TILE}
    assert tiled["12"]["inputs"] == {"samples": ["11", 0], "vae": ["1", 2], **TILE}
    assert tiled["8"]["inputs"]["images"] == ["12", 0]


@pytest.mark.parametrize("tiled", [False, True])
@pytest.mark.parametrize("preset", ["alta", "rapida"])
def test_progress_phases_follow_the_graph(preset: Any, tiled: bool) -> None:
    phases = ProgressMap.of(build_workflow(spec(preset=preset, tiled=tiled)))
    assert phases.loaders == {"1"}
    expected = {"6": "sampling", "11": "refining"} if preset == "alta" else {"6": "sampling"}
    assert phases.samplers == expected


def test_a_build_never_changes_the_template_for_the_next_one() -> None:
    build_workflow(spec(seed=1, positive="first"))
    again = build_workflow(spec(seed=2, positive="second"))
    assert again["6"]["inputs"]["seed"] == again["11"]["inputs"]["seed"] == 2
    assert load_template("sdxl_hires.v1.json")["3"]["inputs"]["text"] == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": -1},
        {"seed": 4294967296},
        {"seed": True},
        {"steps": 9},
        {"steps": 51},
        {"cfg": 0.5},
        {"cfg": 12.5},
        {"refine_steps": 7},
        {"refine_denoise": 0.7},
        {"clip_skip": 3},
        {"scheduler": "exponential"},
        {"sampler": "euler; drop"},
        {"aspect": "huge"},
    ],
)
def test_values_outside_the_documented_ranges_are_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(WorkflowError):
        build_workflow(spec(**changes))
