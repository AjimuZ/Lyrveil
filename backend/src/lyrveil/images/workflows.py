"""Workflow builder: fills the versioned ComfyUI templates of `workflows/comfyui/` with typed values.

Only typed values reach the graph. Text goes into the two `CLIPTextEncode` inputs as data, so a prompt cannot add a node
or a link, and `ckpt_name` comes from the caller's image profile, never from a prompt.
"""

import copy
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal

QualityPreset = Literal["alta", "rapida"]
Aspect = Literal["square", "portrait", "landscape", "tall", "wide"]


class WorkflowError(Exception):
    """A template is missing or malformed, or a spec value is out of range. The first is a packaging fault."""


# docs/image-generation.md (Workflow): the files, one per preset and per tiled variant. A new version of a graph is
# a new file and a new row here; the old file stays valid for stored images.
TEMPLATES: Final[dict[tuple[QualityPreset, bool], str]] = {
    ("rapida", False): "sdxl_base.v2.json",
    ("rapida", True): "sdxl_base_tiled.v2.json",
    ("alta", False): "sdxl_hires.v1.json",
    ("alta", True): "sdxl_hires_tiled.v1.json",
}
TEMPLATE_NAME_PATTERN: Final = re.compile(r"^[a-z0-9_]+\.v[0-9]+\.json$")

# docs/image-generation.md (First pass): the fixed SDXL buckets, as (width, height).
FIRST_PASS_SIZES: Final[dict[Aspect, tuple[int, int]]] = {
    "square": (1024, 1024),
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "tall": (768, 1344),
    "wide": (1344, 768),
}
# ADR 0026: the Alta pixel upscale. The template's `ImageScaleBy` holds the same factor.
ALTA_SCALE: Final = 1.5

STEPS_RANGE: Final = (10, 50)
CFG_RANGE: Final = (1.0, 12.0)
REFINE_STEPS_RANGE: Final = (8, 30)
REFINE_DENOISE_RANGE: Final = (0.3, 0.6)
MAX_SEED: Final = 4294967295
SCHEDULERS: Final = frozenset({"normal", "karras"})
# The sampler's shape only. ComfyUI's own list (`KSAMPLER_NAMES`) is not copied here, so an unknown name that has this
# shape is refused by ComfyUI at validation and surfaces as WorkflowRejectedError.
_SAMPLER_SHAPE: Final = re.compile(r"^[a-z0-9_]{1,40}$")
_CKPT_SHAPE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.safetensors$")

# Node identifiers shared by the four files. A test checks each file against these classes.
_LOADER, _CLIP_SKIP, _POSITIVE, _NEGATIVE, _LATENT, _FIRST_PASS, _SAVE = "1", "2", "3", "4", "5", "6", "8"
_UPSCALE, _SECOND_PASS = "9", "11"


@dataclass(frozen=True)
class WorkflowSpec:
    """Everything a render varies. Defaults are those of docs/image-generation.md (Default parameters).

    `ckpt_name` is the checkpoint file name of the active image profile. `seed` is shared by both passes. `steps`,
    `cfg`, `sampler` and `scheduler` are the first pass's; the second pass of **Alta** reuses `cfg` and `seed`, and
    takes its own steps and denoise.
    """

    preset: QualityPreset
    aspect: Aspect
    ckpt_name: str
    positive: str
    negative: str
    seed: int
    tiled: bool = False
    steps: int = 28
    cfg: float = 5.5
    sampler: str = "euler_ancestral"
    scheduler: str = "normal"
    clip_skip: int = 2
    refine_steps: int = 14
    refine_denoise: float = 0.45


def workflows_dir() -> Path:
    """Returns the templates' folder: `workflows/comfyui` of the repository, or `/app/workflows/comfyui` in an image."""
    here = Path(__file__).resolve()
    for root in (here.parents[4], here.parents[3]):
        candidate = root / "workflows" / "comfyui"
        if candidate.is_dir():
            return candidate
    raise WorkflowError("workflows/comfyui is in neither candidate folder")


@cache
def _template_text(name: str) -> str:
    if name not in TEMPLATES.values():
        raise WorkflowError(f"{name} is not a registered template")
    path = workflows_dir() / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkflowError(f"cannot read template {name}: {error.__class__.__name__}") from error


def load_template(name: str) -> dict[str, Any]:
    """Returns a fresh parsed copy of the registered template `name`; raises WorkflowError if it is unusable."""
    try:
        parsed = json.loads(_template_text(name))
    except json.JSONDecodeError as error:
        raise WorkflowError(f"template {name} is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise WorkflowError(f"template {name} is not an object")
    return parsed


def final_size(preset: QualityPreset, aspect: Aspect) -> tuple[int, int]:
    """Returns the (width, height) of the saved image: the first-pass size, times 1.5 for Alta, a multiple of 8."""
    width, height = FIRST_PASS_SIZES[aspect]
    if preset == "rapida":
        return width, height
    return _round8(width * ALTA_SCALE), _round8(height * ALTA_SCALE)


def build_workflow(spec: WorkflowSpec) -> dict[str, Any]:
    """Returns the API-format graph for `spec`, ready for ImageRequest.workflow.

    Raises WorkflowError when a value is out of the documented range or a template no longer has the expected nodes.
    """
    _validate(spec)
    graph = load_template(TEMPLATES[(spec.preset, spec.tiled)])
    width, height = FIRST_PASS_SIZES[spec.aspect]
    _inputs(graph, _LOADER, "CheckpointLoaderSimple")["ckpt_name"] = spec.ckpt_name
    _inputs(graph, _CLIP_SKIP, "CLIPSetLastLayer")["stop_at_clip_layer"] = -spec.clip_skip
    _inputs(graph, _POSITIVE, "CLIPTextEncode")["text"] = spec.positive
    _inputs(graph, _NEGATIVE, "CLIPTextEncode")["text"] = spec.negative
    latent = _inputs(graph, _LATENT, "EmptyLatentImage")
    latent["width"], latent["height"] = width, height
    first = _inputs(graph, _FIRST_PASS, "KSampler")
    first.update(seed=spec.seed, steps=spec.steps, cfg=spec.cfg, sampler_name=spec.sampler, scheduler=spec.scheduler)
    _inputs(graph, _SAVE, "SaveImage")
    if spec.preset == "alta":
        if _inputs(graph, _UPSCALE, "ImageScaleBy")["scale_by"] != ALTA_SCALE:
            raise WorkflowError("template scale_by differs from ALTA_SCALE")
        second = _inputs(graph, _SECOND_PASS, "KSampler")
        second.update(seed=spec.seed, cfg=spec.cfg, steps=spec.refine_steps, denoise=spec.refine_denoise)
    return copy.deepcopy(graph)


def _round8(value: float) -> int:
    return round(value / 8) * 8


def _inputs(graph: dict[str, Any], node_id: str, class_type: str) -> dict[str, Any]:
    node = graph.get(node_id)
    if not isinstance(node, dict) or node.get("class_type") != class_type or not isinstance(node.get("inputs"), dict):
        raise WorkflowError(f"template node {node_id} is not a {class_type}")
    inputs: dict[str, Any] = node["inputs"]
    return inputs


def _validate(spec: WorkflowSpec) -> None:
    if (spec.preset, spec.tiled) not in TEMPLATES:
        raise WorkflowError("unknown quality preset")
    if spec.aspect not in FIRST_PASS_SIZES:
        raise WorkflowError("unknown aspect")
    if not _CKPT_SHAPE.fullmatch(spec.ckpt_name):
        raise WorkflowError("ckpt_name must be a plain .safetensors file name")
    if isinstance(spec.seed, bool) or not 0 <= spec.seed <= MAX_SEED:
        raise WorkflowError(f"seed must be 0 to {MAX_SEED}")
    _in_range("steps", spec.steps, STEPS_RANGE)
    _in_range("cfg", spec.cfg, CFG_RANGE)
    _in_range("refine_steps", spec.refine_steps, REFINE_STEPS_RANGE)
    _in_range("refine_denoise", spec.refine_denoise, REFINE_DENOISE_RANGE)
    if spec.clip_skip not in (1, 2):
        raise WorkflowError("clip_skip must be 1 or 2")
    if spec.scheduler not in SCHEDULERS:
        raise WorkflowError("scheduler must be normal or karras")
    if not _SAMPLER_SHAPE.fullmatch(spec.sampler):
        raise WorkflowError("sampler is not a sampler name")


def _in_range(name: str, value: float, bounds: tuple[float, float]) -> None:
    if isinstance(value, bool) or not bounds[0] <= value <= bounds[1]:
        raise WorkflowError(f"{name} must be {bounds[0]} to {bounds[1]}")
