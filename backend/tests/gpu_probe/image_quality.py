"""The backend side of T-QLT-02 and T-QLT-07: the renders of the image default grid and of the image identity protocol
with its measured levers, as ComfyUI graphs built with the backend's own prompt builder and workflow builder.

tests/quality/ImageGrid.Tests.ps1 (repository root) runs this file in a container of the backend's test stage, with no
network:

    python tests/gpu_probe/image_quality.py plan < {"profile": ..., "mode": "full" | "short", "tier": "target"}

It prints one JSON line, ASCII only, with the `cells` to render for that catalog image profile. Every cell is one image:
its graph (`workflow`, compact JSON text) and what the review sheet needs to know about it. The graphs use the
prompts of `VisualConsistencyService.build` for fixed characters and scenes, so the grid and the protocol render what
the `render` stage would submit; only the lever cells differ from the stored workflow versions, and only in the way
ADR 0036 asks:

- `grid` cells (every profile): the default settings, and one changed setting at a time (CLIP skip 1, CFG 4.5 and 7,
  second-pass denoise 0.35 and 0.55). On the default profile also the other sizes (landscape, tall and wide).
- `identity` cells (the default profile only): the protocol's characters, scenes and seeds at the default settings, and
  the same renders with weight 1.1 on `hair_color` and `eye_color`.
- `pag` cells (the default profile only): the protocol's renders at the first seed with PerturbedAttentionGuidance on
  the first pass, `scale` 1.5 and 3. Scale 0 is the `identity` baseline.

The PAG node and the identity weight are inserted here, in the test stage, because adopting them is what the
measurement decides: no workflow file or production module changes before then.
"""

import json
import sys
from pathlib import Path
from typing import Any, Final

from lyrveil.catalog import Catalog, ImageProfile, Tier, load_catalog
from lyrveil.domain.card import VisualProfile
from lyrveil.images.visual_consistency import ImageScene, VisualConsistencyService, age_band
from lyrveil.images.workflows import Aspect, WorkflowSpec, build_workflow, final_size, resolve_aspect
from lyrveil.services.demo_characters import DEMO_CARDS
from lyrveil.services.image_generation import ImageSettings

USAGE = "usage: image_quality.py plan < request.json"

# docs/image-generation.md (Grid, Measured levers): the values each lever measures.
CLIP_SKIPS: Final = (1,)
CFG_VALUES: Final = (4.5, 7.0)
DENOISE_VALUES: Final = (0.35, 0.55)
PAG_SCALES: Final = (1.5, 3.0)
IDENTITY_WEIGHT: Final = 1.1
IDENTITY_WEIGHT_SLOTS: Final = ("hair_color", "eye_color")
EXTRA_SIZES: Final[tuple[Aspect, ...]] = ("landscape", "tall", "wide")
GRID_ASPECT: Final[Aspect] = "portrait"
SECTIONS: Final = ("grid", "identity", "pag")

# The seeds are fixed so a run can be repeated. They differ from the preset measurements' (101 to 103, 201 to 203).
SEEDS: Final = (4101, 4102, 4103)

# The size of each mode. The short mode is a smoke run of the same procedure: every kind of cell, one of each.
MODES: Final = {
    "full": {"grid_characters": 3, "grid_scenes": 2, "identity_characters": 5, "identity_scenes": 4, "seeds": 3},
    "short": {"grid_characters": 1, "grid_scenes": 1, "identity_characters": 1, "identity_scenes": 1, "seeds": 1},
}

# The two synthetic characters of the protocol, aged 52 and 70 (ADR 0035, ADR 0036). The three demo characters come from
# the backend's own demo cards, so the protocol renders what a user loads.
SYNTHETIC_CARDS: Final[tuple[dict[str, Any], ...]] = (
    {
        "identity": {"first_name": "Ottavia", "age": 52},
        "visual": {
            "subject_tag": "1girl",
            "identity_en": {
                "hair_color": "brown hair",
                "hair_length": "medium hair",
                "eye_color": "hazel eyes",
                "hairstyle": ["wavy hair"],
                "face": ["glasses"],
                "body": ["curvy"],
                "style": ["soft colors"],
            },
            "usual_outfit_en": ["green cardigan", "long skirt"],
        },
    },
    {
        "identity": {"first_name": "Bruno", "age": 70},
        "visual": {
            "subject_tag": "1boy",
            "identity_en": {
                "hair_color": "white hair",
                "hair_length": "short hair",
                "eye_color": "blue eyes",
                "face": ["beard"],
                "body": ["stocky"],
                "style": ["muted colors"],
            },
            "usual_outfit_en": ["brown vest", "white shirt"],
        },
    },
)

# The four scenes of the protocol, fixed, with vocabulary tags. The first two are the grid's scenes; the framings differ
# so `auto` gives portrait for three of them and tall for the full-body one. Scenes written by every text profile are
# not rendered here: the text profiles are not run by this probe (docs/testing.md, T-QLT-07).
SCENES: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    (
        "cafe",
        {
            "framing": "upper body",
            "expression": ["smile", "looking at viewer"],
            "pose": ["sitting", "holding cup"],
            "location": ["cafe", "window"],
            "time_of_day": "day",
            "lighting": ["warm lighting"],
        },
    ),
    (
        "street",
        {
            "framing": "full body",
            "expression": ["calm"],
            "pose": ["standing", "walking"],
            "location": ["city street", "stone paving"],
            "time_of_day": "night",
            "lighting": ["dramatic lighting"],
        },
    ),
    (
        "park",
        {
            "framing": "cowboy shot",
            "expression": ["light smile"],
            "pose": ["standing", "hand in pocket"],
            "location": ["park", "trees"],
            "time_of_day": "day",
            "lighting": ["sunlight"],
        },
    ),
    (
        "library",
        {
            "framing": "portrait",
            "expression": ["thinking"],
            "pose": ["reading", "holding book"],
            "location": ["library", "bookshelves"],
            "time_of_day": "twilight",
            "lighting": ["dim lighting"],
        },
    ),
)

_SCENE_DEFAULTS: Final[dict[str, Any]] = {
    "schema_version": 1,
    "angle": None,
    "outfit": "usual",
    "scene_clothing": [],
    "objects": [],
    "extra_negative": [],
}

# Node identifiers of the templates (workflows.py). The PAG node takes a free identifier.
_FIRST_PASS_NODE: Final = "6"
_PAG_NODE: Final = "20"


def _catalog() -> Catalog:
    """Returns the catalog the backend ships: /app/config in the test image, config/ of the repository otherwise."""
    here = Path(__file__).resolve()
    for root in (here.parents[2], here.parents[3]):
        candidate = root / "config" / "model-catalog.json"
        if candidate.is_file():
            return load_catalog(candidate)
    raise FileNotFoundError("config/model-catalog.json is in neither candidate folder")


def _profile(catalog: Catalog, profile_id: str) -> ImageProfile:
    """Returns the catalog image profile `profile_id`. Raises KeyError when the catalog has none."""
    for profile in catalog.image:
        if profile.id == profile_id:
            return profile
    raise KeyError(f"the catalog has no image profile {profile_id}")


def characters() -> list[tuple[str, int, VisualProfile]]:
    """Returns the protocol's five characters as (first name, age, visual profile): the three demo characters, then the
    synthetic ones aged 52 and 70."""
    people: list[tuple[str, int, VisualProfile]] = []
    for card in [*DEMO_CARDS, *SYNTHETIC_CARDS]:
        identity = card["identity"]
        visual = VisualProfile.model_validate(card["visual"])
        people.append((str(identity["first_name"]), int(identity["age"]), visual))
    return people


def scenes() -> list[tuple[str, ImageScene]]:
    """Returns the protocol's four scenes as (name, scene)."""
    return [(name, ImageScene.model_validate({**_SCENE_DEFAULTS, **fields})) for name, fields in SCENES]


def apply_identity_weight(positive: str, visual: VisualProfile) -> str:
    """Returns `positive` with the tags of the `hair_color` and `eye_color` slots written as `(tag:1.1)`. Only an entire
    tag of the comma-separated prompt is wrapped, so a longer tag that holds the same words is left alone."""
    wanted: set[str] = set()
    for slot in IDENTITY_WEIGHT_SLOTS:
        tag = getattr(visual.identity_en, slot)
        if tag is not None:
            wanted.add(str(tag))
    tags = [f"({tag}:{IDENTITY_WEIGHT})" if tag in wanted else tag for tag in positive.split(", ")]
    return ", ".join(tags)


def insert_pag(graph: dict[str, Any], scale: float) -> dict[str, Any]:
    """Returns `graph` with a PerturbedAttentionGuidance node of `scale` between the checkpoint and the first pass's
    KSampler. The second pass of Alta keeps the plain model. Raises ValueError when the first pass is not node 6."""
    first = graph.get(_FIRST_PASS_NODE)
    if not isinstance(first, dict) or first.get("class_type") != "KSampler":
        raise ValueError("node 6 is not the first pass's KSampler")
    graph[_PAG_NODE] = {"class_type": "PerturbedAttentionGuidance", "inputs": {"model": ["1", 0], "scale": scale}}
    first["inputs"]["model"] = [_PAG_NODE, 0]
    return graph


def plan_document(request: dict[str, Any]) -> dict[str, Any]:
    """Returns the cells to render for the catalog image profile `profile` in `mode`, for the `tier` whose default image
    profile gets the lever cells (see the module text). Each cell: `id` (unique), `section`, `variant`, `character`,
    `scene`, `seed`, `aspect`, `width`, `height`, `pair` (the cell is one side of a blind pair), `expected` (identity
    cells: the attributes the owner checks) and `workflow`. A request with `section` gets only the cells of that
    section: the caller asks for one section at a time, so no answer is longer than Windows PowerShell 5.1 parses.

    Raises KeyError for a missing field or an unknown profile and ValueError for an unknown mode or section.
    """
    mode = str(request["mode"])
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    only = request.get("section")
    if only is not None and only not in SECTIONS:
        raise ValueError(f"unknown section {only}")
    shape = MODES[mode]
    catalog = _catalog()
    profile = _profile(catalog, str(request["profile"]))
    tier: Tier = request["tier"]
    is_default = catalog.default_image(tier).id == profile.id
    settings = ImageSettings.for_profile(profile, "alta")
    service = VisualConsistencyService()
    people = characters()
    stages = scenes()
    seeds = SEEDS[: int(shape["seeds"])]
    cells: list[dict[str, Any]] = []

    def add(
        section: str,
        variant: str,
        person: tuple[str, int, VisualProfile],
        scene: tuple[str, ImageScene],
        seed: int,
        *,
        aspect: Aspect,
        cfg: float | None = None,
        clip_skip: int | None = None,
        denoise: float | None = None,
        weighted: bool = False,
        pag: float | None = None,
        pair: bool = False,
    ) -> None:
        name, age, visual = person
        prompt = service.build(visual, age, scene[1], settings.preset)
        positive = apply_identity_weight(prompt.positive, visual) if weighted else prompt.positive
        spec = WorkflowSpec(
            preset="alta",
            aspect=aspect,
            ckpt_name=settings.checkpoint_file,
            positive=positive,
            negative=prompt.negative,
            seed=seed,
            steps=settings.steps,
            cfg=settings.cfg if cfg is None else cfg,
            sampler=settings.sampler,
            scheduler=settings.scheduler,
            clip_skip=settings.clip_skip if clip_skip is None else clip_skip,
            refine_steps=settings.refine_steps,
            refine_denoise=settings.refine_denoise if denoise is None else denoise,
        )
        graph = build_workflow(spec)
        if pag is not None:
            graph = insert_pag(graph, pag)
        width, height = final_size("alta", aspect)
        cell: dict[str, Any] = {
            "id": f"{section}/{variant}/{name}/{scene[0]}/{seed}",
            "section": section,
            "variant": variant,
            "character": name,
            "scene": scene[0],
            "seed": seed,
            "aspect": aspect,
            "width": width,
            "height": height,
            "pair": pair,
            "workflow": json.dumps(graph, ensure_ascii=True, separators=(",", ":")),
        }
        if section == "identity":
            slots = visual.identity_en
            cell["expected"] = {
                "hair_color": slots.hair_color,
                "hair_length": slots.hair_length,
                "eye_color": slots.eye_color,
                "age": age,
                "age_band": ", ".join(age_band(age, visual.subject_tag)),
                "outfit": ", ".join(visual.usual_outfit_en),
            }
        cells.append(cell)

    for person in people[: int(shape["grid_characters"])]:
        for scene in stages[: int(shape["grid_scenes"])]:
            for seed in seeds:
                add("grid", "default", person, scene, seed, aspect=GRID_ASPECT)
                for clip_skip in CLIP_SKIPS:
                    add("grid", f"clip-skip-{clip_skip}", person, scene, seed, aspect=GRID_ASPECT, clip_skip=clip_skip)
                for cfg in CFG_VALUES:
                    add("grid", f"cfg-{cfg:g}", person, scene, seed, aspect=GRID_ASPECT, cfg=cfg)
                for denoise in DENOISE_VALUES:
                    add("grid", f"denoise-{denoise:g}", person, scene, seed, aspect=GRID_ASPECT, denoise=denoise)
                if is_default:
                    for size in EXTRA_SIZES:
                        add("grid", size, person, scene, seed, aspect=size)

    if is_default:
        for person in people[: int(shape["identity_characters"])]:
            for scene in stages[: int(shape["identity_scenes"])]:
                aspect = resolve_aspect("auto", scene[1].framing)
                for index, seed in enumerate(seeds):
                    first = index == 0
                    add("identity", "baseline", person, scene, seed, aspect=aspect)
                    add(
                        "identity",
                        f"identity-weight-{IDENTITY_WEIGHT:g}",
                        person,
                        scene,
                        seed,
                        aspect=aspect,
                        weighted=True,
                        pair=first,
                    )
                    if first:
                        for scale in PAG_SCALES:
                            add("pag", f"pag-{scale:g}", person, scene, seed, aspect=aspect, pag=scale, pair=True)

    return {
        "profile": profile.id,
        "checkpoint": settings.checkpoint_file,
        "prompt_preset": settings.preset,
        "tier": tier,
        "default_profile": catalog.default_image(tier).id,
        "is_default": is_default,
        "mode": mode,
        "quality": "alta",
        "cells": [cell for cell in cells if only is None or cell["section"] == only],
    }


def main(arguments: list[str]) -> int:
    """Runs the action named by `arguments` on the JSON object of standard input and prints its document. Returns 2,
    printing the usage, for a wrong action, and 0 otherwise; a malformed request raises, so the process fails."""
    if len(arguments) != 1 or arguments[0] != "plan":
        print(USAGE, file=sys.stderr)
        return 2
    request = json.loads(sys.stdin.read())
    print(json.dumps(plan_document(request), ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
