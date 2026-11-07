"""VisualConsistencyService without a runtime: prompt composition and the safety negative (T-IMG-01), the scene
filter's steps in order (T-IMG-02), conflicting visual tags (T-CON-07), and the identity segment that every template
family's scene leaves byte-identical, with its order, age band and caps (T-IMG-15)."""

from typing import Any, get_args

import pytest

from lyrveil.catalog import ImagePreset, TextTemplate
from lyrveil.domain.card import SubjectTag, VisualProfile
from lyrveil.domain.errors import ContentPolicyBlockedError
from lyrveil.images.visual_consistency import (
    CHUNK_TOKENS,
    IDENTITY_CAP,
    OUTFIT_CAP,
    PROMPT_PRESETS,
    SAFETY_NEGATIVE,
    SCENE_CAP,
    ImageScene,
    Removal,
    VisualConsistencyService,
    age_band,
)
from lyrveil.services.card_rules import validate_card

PRESETS: tuple[ImagePreset, ...] = get_args(ImagePreset)
FAMILIES: tuple[TextTemplate, ...] = get_args(TextTemplate)
SERVICE = VisualConsistencyService()


def nerea(**visual: Any) -> VisualProfile:
    """The visual profile of the example in docs/image-generation.md (Final prompt), validated as a card."""
    raw: dict[str, Any] = {
        "subject_tag": "1girl",
        "identity_en": {
            "hair_color": "red hair",
            "hair_length": "short hair",
            "eye_color": "green eyes",
            "hairstyle": ["messy hair"],
            "face": ["freckles"],
            "body": ["slim"],
            "marks": ["ink stains"],
            "accessories": ["necklace"],
            "style": ["soft colors"],
            "extra": ["brass compass pendant"],
        },
        "usual_outfit_en": ["canvas jacket", "many pockets"],
        "outfits": [{"key": "festa", "label": "Festa", "tags_en": ["green dress", "silver earrings"]}],
        **visual,
    }
    return validate_card({"identity": {"first_name": "Nerea", "age": 27}, "visual": raw}).visual


def scene(**fields: Any) -> ImageScene:
    """The harbour scene of the same example; `fields` replace its values."""
    values: dict[str, Any] = {
        "schema_version": 1,
        "framing": "upper body",
        "angle": None,
        "expression": ["smile"],
        "pose": ["sitting"],
        "outfit": "usual",
        "scene_clothing": [],
        "location": ["harbor"],
        "time_of_day": "sunset",
        "lighting": ["warm lighting"],
        "objects": ["old map", "table"],
        "extra_negative": [],
    }
    return ImageScene.model_validate({**values, **fields})


def reasons(service: VisualConsistencyService, image_scene: ImageScene, visual: VisualProfile) -> dict[str, Removal]:
    return {removed.tag: removed.reason for removed in service.filter_scene(image_scene, visual).removed}


def test_the_documented_example_is_built_exactly_and_the_same_way_every_time() -> None:
    first = SERVICE.build(nerea(), 27, scene(), "illustrious")
    assert first.positive == (
        "masterpiece, best quality, amazing quality, absurdres, general, 1girl, adult, woman, red hair, short hair, "
        "messy hair, green eyes, freckles, slim, ink stains, canvas jacket, many pockets, necklace, smile, sitting, "
        "upper body, harbor, sunset, warm lighting, old map, table, soft colors, brass compass pendant"
    )
    assert first.outfit_key == "usual"
    assert VisualConsistencyService().build(nerea(), 27, scene(), "illustrious") == first


@pytest.mark.parametrize("preset", PRESETS)
def test_the_safety_negative_is_always_present_in_order_after_the_base_negative(preset: ImagePreset) -> None:
    visual = nerea(persistent_negative_en="(blurry:1.4), child, extra arms")
    prompt = SERVICE.build(visual, 27, scene(extra_negative=["nsfw", "rain", "embedding:bad"]), preset)
    base = list(PROMPT_PRESETS[preset].base_negative)
    assert prompt.negative.split(", ") == [*base, *SAFETY_NEGATIVE, "blurry1.4", "extra arms", "rain"]
    bare = SERVICE.build(nerea(), 27, scene(), preset)
    assert bare.negative == ", ".join([*base, *SAFETY_NEGATIVE])


def test_the_scene_filter_runs_its_steps_in_the_documented_order() -> None:
    dropped = reasons(
        SERVICE,
        scene(
            expression=["Smile", "smile", "grinning widely"],
            location=["  ", "one two three four five", "Red_Hair", "blonde hair", "capelli biondi", "dimples"],
            objects=["(old map:1.5)", "sunglasses"],
        ),
        nerea(locked_fields=["accessories"]),
    )
    assert dropped == {
        "smile": Removal.DUPLICATE,
        "grinning widely": Removal.UNKNOWN,
        "": Removal.EMPTY,
        "one two three four five": Removal.TOO_LONG,
        "red hair": Removal.IN_IDENTITY,
        "blonde hair": Removal.IDENTITY_CATEGORY,
        "capelli biondi": Removal.IDENTITY_CATEGORY,
        "dimples": Removal.IDENTITY_CATEGORY,
        "sunglasses": Removal.LOCKED,
    }
    kept = SERVICE.filter_scene(scene(objects=["(old map:1.5)", "sunglasses"]), nerea())
    assert kept.objects == ("old map1.5", "sunglasses")


@pytest.mark.parametrize(
    "fields",
    [
        {"location": ["playground", "little girl"]},
        {"objects": ["nsfw"]},
        {"location": ["loli hair"]},  # Blocked at step 3, before step 4 would drop it as a hair tag.
        {"scene_clothing": ["school uniform", "schoolgirl outfit"], "outfit": "scene"},
    ],
)
def test_a_blocked_term_fails_the_scene_without_quoting_it(fields: dict[str, Any]) -> None:
    with pytest.raises(ContentPolicyBlockedError) as blocked:
        SERVICE.build(nerea(), 27, scene(**fields), "illustrious")
    assert blocked.value.code == "CONTENT_POLICY_BLOCKED"
    assert not any(word in blocked.value.detail for word in ("girl", "nsfw", "loli"))


def test_a_long_tag_is_dropped_at_step_two_before_the_blocked_terms() -> None:
    assert reasons(SERVICE, scene(location=["a park with a little girl"]), nerea()) == {
        "a park with a little girl": Removal.TOO_LONG
    }


def test_conflicting_visual_tags_never_reach_the_prompt() -> None:
    conflicting = scene(
        expression=["closed eyes"],
        location=["blonde hair", "long blonde hair", "(blue eyes:1.6)", "dark skin", "very long hair", "beard"],
        objects=["red hair1.5", "embedding:evil"],
        extra_negative=["red hair", "woman", "freckles", "green eyes", "rain"],
    )
    prompt = SERVICE.build(nerea(), 27, conflicting, "illustrious")
    positive = prompt.positive.split(", ")
    for absent in ("blonde hair", "long blonde hair", "blue eyes1.6", "dark skin", "very long hair", "beard"):
        assert absent not in positive
    assert "red hair1.5" not in positive
    assert positive.count("red hair") == 1
    assert "closed eyes" in positive  # An expression, not an eye colour: the vocabulary decides.
    assert "evil" not in positive
    assert "embedding" not in prompt.positive
    negative = prompt.negative.split(", ")
    assert "rain" in negative
    for kept_in_positive in ("red hair", "woman", "freckles", "green eyes"):
        assert kept_in_positive not in negative


@pytest.mark.parametrize(
    ("age", "subject", "band"),
    [
        (18, "1girl", ("woman",)),
        (29, "1boy", ("man",)),
        (30, "1other", ("mature",)),
        (44, "1girl", ("mature female",)),
        (45, "1boy", ("mature male", "middle-aged")),
        (64, "1other", ("mature", "middle-aged")),
        (65, "1girl", ("old woman", "wrinkles")),
        (120, "1boy", ("old man", "wrinkles")),
    ],
)
def test_the_age_band_follows_adult(age: int, subject: SubjectTag, band: tuple[str, ...]) -> None:
    assert age_band(age, subject) == band
    visual = nerea(subject_tag=subject)
    identity = SERVICE.build(visual, age, scene(), "noobai").identity.split(", ")
    start = identity.index("adult")
    assert identity[start - 1 : start + 1 + len(band)] == [subject, "adult", *band]
    assert identity[:6] == ["masterpiece", "best quality", "newest", "absurdres", "highres", "safe"]


# One scene per template family, as each might word it: different tags, conflicting ones and prompt syntax included.
FAMILY_SCENES: dict[TextTemplate, dict[str, Any]] = {
    "gemma4": {"expression": ["smile"], "location": ["harbor"], "objects": ["old map"]},
    "mistral_v3_tekken": {
        "framing": "full body",
        "angle": "from side",
        "expression": ["light smile", "blush"],
        "pose": ["standing", "crossed arms"],
        "location": ["pier", "blonde hair", "(masterpiece:2)"],
        "lighting": ["backlighting"],
        "objects": ["seagulls", "embedding:style"],
    },
    "chatml": {
        "framing": "close-up",
        "expression": ["serious"],
        "pose": [],
        "outfit": "festa",
        "location": ["ballroom", "very long hair", "blue eyes"],
        "time_of_day": "night",
        "lighting": ["candlelight"],
        "objects": [],
        "extra_negative": ["red hair"],
    },
}


@pytest.mark.parametrize("preset", PRESETS)
def test_the_identity_segment_is_byte_identical_for_every_template_family(preset: ImagePreset) -> None:
    assert set(FAMILY_SCENES) == set(FAMILIES)
    visual = nerea()
    prompts = [SERVICE.build(visual, 52, scene(**FAMILY_SCENES[family]), preset) for family in FAMILIES]
    identities = {prompt.identity for prompt in prompts}
    assert len(identities) == 1
    (identity,) = identities
    assert all(prompt.positive.startswith(identity + ", ") for prompt in prompts)
    assert identity == ", ".join(SERVICE.identity_segment(visual, 52, preset))
    assert identity.endswith(
        "1girl, adult, mature female, middle-aged, "
        "red hair, short hair, messy hair, green eyes, freckles, slim, ink stains"
    )
    assert len({prompt.identity_tokens for prompt in prompts}) == 1


def test_identity_follows_the_fixed_slot_order_and_its_cap() -> None:
    visual = nerea(
        identity_en={
            "marks": ["scar", "mole", "tattoo", "birthmark"],
            "body": ["tall", "athletic", "toned", "broad shoulders"],
            "face": ["dimples", "freckles", "strong jaw", "full lips"],
            "skin": "tan",
            "eye_color": "amber eyes",
            "hairstyle": ["ponytail", "bangs", "wavy hair", "sidelocks"],
            "hair_length": "long hair",
            "hair_color": "black hair",
        }
    )
    tags = SERVICE.identity_tags(visual)
    assert len(tags) == IDENTITY_CAP
    assert tags[:8] == ("black hair", "long hair", "ponytail", "bangs", "wavy hair", "sidelocks", "amber eyes", "tan")
    assert tags[8:12] == ("dimples", "freckles", "strong jaw", "full lips")
    assert tags[12:] == ("tall", "athletic", "toned", "broad shoulders")


def test_outfit_and_scene_caps_and_the_first_chunk_flag() -> None:
    visual = nerea(
        usual_outfit_en=[f"garment {n}" for n in range(12)],
        identity_en={"accessories": ["necklace", "watch"], "hair_color": "red hair"},
    )
    busy = scene(
        expression=["smile", "blush", "wink"],
        pose=["standing", "waving", "head tilt", "hand in pocket"],
        angle="from above",
        location=[f"place {n}" for n in range(6)],
        lighting=["sunlight", "rim light", "light rays"],
        objects=[f"thing {n}" for n in range(4)],
    )
    prompt = SERVICE.build(visual, 27, busy, "illustrious")
    positive = prompt.positive.split(", ")
    assert sum(tag.startswith("garment") or tag in {"necklace", "watch"} for tag in positive) == OUTFIT_CAP
    assert "necklace" not in positive  # Accessories come after the outfit, so the cap drops them first.
    # This profile has no style or extra, so the scene ends the prompt. Objects come last in it, so the cap drops
    # them first, then the third lighting tag.
    scene_part = positive[positive.index("smile") :]
    assert len(scene_part) == SCENE_CAP
    assert scene_part[-2:] == ["sunlight", "rim light"]
    assert prompt.identity_in_first_chunk
    assert prompt.identity_tokens <= CHUNK_TOKENS < prompt.tokens


@pytest.mark.parametrize(
    ("fields", "locked", "key", "present"),
    [
        ({"outfit": "festa"}, [], "festa", "green dress"),
        ({"outfit": "scene", "scene_clothing": ["raincoat", "rubber boots"]}, [], "scene", "raincoat"),
        ({"outfit": "scene", "scene_clothing": ["raincoat"]}, ["clothing"], "usual", "canvas jacket"),
        ({"outfit": "scene", "scene_clothing": ["blonde hair"]}, [], "usual", "canvas jacket"),
        ({"outfit": "missing"}, [], "usual", "canvas jacket"),
    ],
)
def test_the_outfit_follows_the_scene_unless_clothing_is_locked(
    fields: dict[str, Any], locked: list[str], key: str, present: str
) -> None:
    prompt = SERVICE.build(nerea(locked_fields=locked), 27, scene(**fields), "illustrious")
    assert prompt.outfit_key == key
    assert present in prompt.positive.split(", ")
    if key != "scene":
        assert "raincoat" not in prompt.positive
