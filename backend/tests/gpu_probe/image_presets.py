"""The backend side of T-GPU-06 and the real-GPU part of T-IMG-14: the render graphs of each image profile and preset,
and the preset rule of docs/image-generation.md computed from the measured render times.

tests/gpu/ImageProfiles.Tests.ps1 (repository root) runs this file in a container of the backend's test stage, with no
network. So the gpu level renders exactly the graph the `render` stage submits for a profile and a preset
(`build_workflow` with `ImageSettings.for_profile`), and the rule is code with unit tests, not a hand computation:

    python tests/gpu_probe/image_presets.py workflow < {"profile": ..., "quality": ..., "aspect": ..., "seeds": [...]}
    python tests/gpu_probe/image_presets.py rule < {"tier": ..., "profiles": [{"profile": ..., "alta_seconds": [...],
                                                     "rapida_seconds": [...]}]}

Each action reads one JSON object from standard input and prints one JSON line, ASCII only, so Windows PowerShell 5.1
reads it byte for byte.
"""

import json
import math
import sys
from pathlib import Path
from typing import Any, Final

from lyrveil.catalog import Catalog, ImageProfile, Tier, load_catalog
from lyrveil.images.visual_consistency import PROMPT_PRESETS, SAFETY_NEGATIVE
from lyrveil.images.workflows import TEMPLATES, Aspect, QualityPreset, WorkflowSpec, build_workflow, final_size
from lyrveil.services.image_generation import ImageSettings

USAGE = "usage: image_presets.py workflow|rule < request.json"

# ADR 0026 and docs/image-generation.md (Quality presets): Alta stays the default when the 95th percentile of its
# render stage is at most 45 s on the reference machine; otherwise Rapida becomes the default.
ALTA_P95_LIMIT_S: Final = 45.0
RULE_PERCENTILE: Final = 95.0

# A fixed, neutral scene of an adult character, so every profile and preset renders the same request. The preset's
# quality and rating tags and the negative prompt come from the backend (PROMPT_PRESETS, SAFETY_NEGATIVE).
SCENE_TAGS: Final = (
    "1girl, woman, solo, long brown hair, green eyes, cardigan, sitting, reading a book, bookshop, night, warm light"
)


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


def workflow_document(request: dict[str, Any]) -> dict[str, Any]:
    """Returns the graphs the `render` stage submits for the catalog image profile `profile` at `quality` and `aspect`,
    one per seed of `seeds`, each as compact ASCII JSON text (`workflows`), with the checkpoint, the template file, the
    prompt preset, the sampler values and the final (width, height) the saved image must have.

    Raises KeyError for a missing field or an unknown profile, and WorkflowError for a value out of range.
    """
    catalog = _catalog()
    quality: QualityPreset = request["quality"]
    aspect: Aspect = request["aspect"]
    settings = ImageSettings.for_profile(_profile(catalog, str(request["profile"])), quality)
    tags = PROMPT_PRESETS[settings.preset]
    positive = ", ".join([*tags.quality, tags.rating, SCENE_TAGS])
    negative = ", ".join([*tags.base_negative, *SAFETY_NEGATIVE])
    graphs = []
    for seed in request["seeds"]:
        spec = WorkflowSpec(
            preset=quality,
            aspect=aspect,
            ckpt_name=settings.checkpoint_file,
            positive=positive,
            negative=negative,
            seed=int(seed),
            steps=settings.steps,
            cfg=settings.cfg,
            sampler=settings.sampler,
            scheduler=settings.scheduler,
            clip_skip=settings.clip_skip,
            refine_steps=settings.refine_steps,
            refine_denoise=settings.refine_denoise,
        )
        graphs.append(json.dumps(build_workflow(spec), ensure_ascii=True, separators=(",", ":")))
    width, height = final_size(quality, aspect)
    return {
        "profile": settings.profile_id,
        "checkpoint": settings.checkpoint_file,
        "template": TEMPLATES[(quality, False)],
        "prompt_preset": settings.preset,
        "quality": quality,
        "aspect": aspect,
        "steps": settings.steps,
        "refine_steps": settings.refine_steps if quality == "alta" else 0,
        "sampler": settings.sampler,
        "width": width,
        "height": height,
        "workflows": graphs,
    }


def nearest_rank(values: list[float], percentile: float) -> float:
    """Returns the nearest-rank percentile of `values`: the smallest value with at least `percentile` % of the values
    at or below it. With fewer than 20 values the 95th percentile is the largest one, which errs towards Rapida.

    Raises ValueError when `values` is empty or `percentile` is not above 0 and at most 100.
    """
    if not values:
        raise ValueError("no values")
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be above 0 and at most 100")
    ordered = sorted(values)
    rank = math.ceil(percentile / 100 * len(ordered))
    return ordered[rank - 1]


def _verdict(entry: dict[str, Any]) -> dict[str, Any]:
    """Returns the rule's verdict for one profile's measurements: the 95th percentiles, and `preset` "alta" when the
    Alta percentile is at most the limit, "rapida" when it is above, or "undecided" when no Alta render was timed."""
    alta = [float(value) for value in entry.get("alta_seconds") or []]
    rapida = [float(value) for value in entry.get("rapida_seconds") or []]
    verdict: dict[str, Any] = {
        "profile": str(entry["profile"]),
        "alta_samples": len(alta),
        "rapida_samples": len(rapida),
        "alta_p95_s": round(nearest_rank(alta, RULE_PERCENTILE), 2) if alta else None,
        "rapida_p95_s": round(nearest_rank(rapida, RULE_PERCENTILE), 2) if rapida else None,
        "preset": "undecided",
    }
    if verdict["alta_p95_s"] is not None:
        verdict["preset"] = "alta" if verdict["alta_p95_s"] <= ALTA_P95_LIMIT_S else "rapida"
    return verdict


def rule_document(request: dict[str, Any]) -> dict[str, Any]:
    """Returns the preset rule applied to the measured render times of each profile of `profiles`, and the default
    quality preset it gives: the verdict of the catalog's default image profile of `tier`, or "undecided" when that
    profile was not measured or has no timed Alta render.

    Render times are the seconds of the render stage with the checkpoint already loaded; the caller leaves out the
    first render after a start, which also loads the checkpoint (docs/testing.md, T-GPU-06). Raises KeyError for a
    missing field and ValueError for a malformed time.
    """
    tier: Tier = request["tier"]
    default_profile = _catalog().default_image(tier).id
    verdicts = [_verdict(entry) for entry in request["profiles"]]
    measured = {verdict["profile"]: verdict for verdict in verdicts}
    default = measured.get(default_profile)
    return {
        "rule": "Alta stays the default when the 95th percentile of its render stage is at most 45 s (ADR 0026)",
        "limit_s": ALTA_P95_LIMIT_S,
        "percentile": RULE_PERCENTILE,
        "method": "nearest rank",
        "tier": tier,
        "default_profile": default_profile,
        "default_preset": "undecided" if default is None else default["preset"],
        "profiles": verdicts,
    }


def main(arguments: list[str]) -> int:
    """Runs the action named by `arguments` on the JSON object of standard input and prints its document. Returns 2,
    printing the usage, for a wrong action, and 0 otherwise; a malformed request raises, so the process fails."""
    if len(arguments) != 1 or arguments[0] not in ("workflow", "rule"):
        print(USAGE, file=sys.stderr)
        return 2
    request = json.loads(sys.stdin.read())
    document = workflow_document(request) if arguments[0] == "workflow" else rule_document(request)
    print(json.dumps(document, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
