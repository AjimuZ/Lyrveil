"""T-IMG-17 without a model: the ImageScene grammar of each request limits `outfit` to the wardrobe and drops `scene`
when `clothing` is locked, aspect `auto` maps each framing to its size with Alta sizes in multiples of 8, and an
output still invalid after the repair gives the fallback scene."""

import json
from typing import Any, get_args

import pytest

from lyrveil.domain.card import CharacterCard
from lyrveil.images.tags import load_vocabulary
from lyrveil.images.visual_consistency import Framing, ImageScene
from lyrveil.images.workflows import FIRST_PASS_SIZES, final_size, resolve_aspect
from lyrveil.ports.memory_retriever import RetrievalQuery, RetrievalResult
from lyrveil.prompts.image_scene import (
    EXAMPLE_OUTFITS,
    EXAMPLE_RESULT,
    fallback_scene,
    outfit_choices,
    render_prompt,
    request_tags,
    scene_grammar,
)
from lyrveil.services.card_rules import validate_card
from lyrveil.services.image_prompt import ImagePromptRequest, ImagePromptStage
from unit.test_character_draft import matches
from unit.test_visual_profile_translation import ScriptedRuntime

VOCABULARY = load_vocabulary()


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def result(**fields: Any) -> dict[str, Any]:
    return {**EXAMPLE_RESULT, **fields}


def card(**visual: Any) -> CharacterCard:
    raw: dict[str, Any] = {
        "subject_tag": "1girl",
        "clothing": "Giacca di tela con molte tasche.",
        "usual_outfit_en": ["canvas jacket"],
        "outfits": [{"key": "festa", "label": "Festa", "tags_en": ["green dress"]}],
        **visual,
    }
    return validate_card({"identity": {"first_name": "Nerea", "age": 27}, "visual": raw})


class NoMemories:
    """A MemoryRetriever for tests that never build a reply prompt."""

    def retrieve_relevant_memories(self, query: RetrievalQuery) -> RetrievalResult:
        return RetrievalResult(items=(), token_cost=0, candidate_count=0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_outfit_choices_follow_the_wardrobe_the_lock_and_the_form() -> None:
    assert outfit_choices(card().visual) == ("usual", "festa", "scene")
    assert outfit_choices(card(locked_fields=["clothing"]).visual) == ("usual", "festa")
    assert outfit_choices(card().visual, "festa") == ("festa",)
    with pytest.raises(ValueError, match="not one the card offers"):
        outfit_choices(card(locked_fields=["clothing"]).visual, "scene")
    with pytest.raises(ValueError, match="not one the card offers"):
        outfit_choices(card().visual, "gala")


def test_the_grammar_limits_outfit_to_the_wardrobe_and_accepts_the_worked_example() -> None:
    grammar = scene_grammar(VOCABULARY, outfit_choices(card().visual))
    assert outfit_choices(card().visual) == EXAMPLE_OUTFITS
    assert matches(grammar, compact(EXAMPLE_RESULT))
    assert matches(grammar, compact(result(outfit="festa")))
    assert matches(grammar, compact(result(outfit="scene", scene_clothing=["raincoat"])))
    assert not matches(grammar, compact(result(outfit="gala")))
    assert not matches(grammar, compact(result(expression=["red hair"])))  # Not an expression tag.
    assert not matches(grammar, compact(result(lighting=["warm lighting"] * 4)))  # Over the bound of 3.
    assert scene_grammar(VOCABULARY, EXAMPLE_OUTFITS) == grammar


def test_a_locked_clothing_drops_scene_and_its_clothing() -> None:
    grammar = scene_grammar(VOCABULARY, outfit_choices(card(locked_fields=["clothing"]).visual))
    assert matches(grammar, compact(result(outfit="festa")))
    assert not matches(grammar, compact(result(outfit="scene")))
    assert not matches(grammar, compact(result(scene_clothing=["raincoat"])))
    chosen = scene_grammar(VOCABULARY, ("festa",))
    assert matches(chosen, compact(result(outfit="festa")))
    assert not matches(chosen, compact(EXAMPLE_RESULT))


@pytest.mark.parametrize(
    ("framing", "aspect", "rapida", "alta"),
    [
        ("close-up", "portrait", (832, 1216), (1248, 1824)),
        ("portrait", "portrait", (832, 1216), (1248, 1824)),
        ("upper body", "portrait", (832, 1216), (1248, 1824)),
        ("cowboy shot", "portrait", (832, 1216), (1248, 1824)),
        ("full body", "tall", (768, 1344), (1152, 2016)),
        ("wide shot", "wide", (1344, 768), (2016, 1152)),
    ],
)
def test_auto_maps_each_framing_to_its_size(
    framing: Framing, aspect: str, rapida: tuple[int, int], alta: tuple[int, int]
) -> None:
    resolved = resolve_aspect("auto", framing)
    assert resolved == aspect
    assert final_size("rapida", resolved) == rapida
    assert final_size("alta", resolved) == alta


def test_every_framing_is_mapped_a_fixed_aspect_wins_and_alta_sizes_are_multiples_of_8() -> None:
    for framing in get_args(Framing):
        assert resolve_aspect("auto", framing) in FIRST_PASS_SIZES
        assert resolve_aspect("square", framing) == "square"
    for aspect in FIRST_PASS_SIZES:
        width, height = final_size("alta", aspect)
        assert (width % 8, height % 8) == (0, 0)


def test_request_tags_take_vocabulary_words_and_prefer_the_longer_phrase() -> None:
    found = request_tags("Mandami una foto con un sorriso leggero, seduta, in controluce", VOCABULARY)
    assert found["expression"] == ("light smile",)
    assert found["pose"] == ("sitting",)
    assert found["lighting"] == ("backlighting",)
    assert request_tags("una foto qualsiasi", VOCABULARY) == {"expression": (), "pose": (), "lighting": ()}


def test_the_fallback_scene_is_upper_body_in_the_usual_outfit_with_the_request_words() -> None:
    scene = fallback_scene("fammi vedere come sorridi, seduta al tavolo", VOCABULARY)
    assert isinstance(scene, ImageScene)
    assert (scene.framing, scene.angle, scene.outfit) == ("upper body", None, "usual")
    assert scene.pose == ["sitting"]
    assert scene.location == scene.objects == scene.scene_clothing == scene.extra_negative == []


@pytest.mark.anyio
async def test_invalid_output_after_the_repair_leads_to_the_fallback_scene() -> None:
    runtime = ScriptedRuntime(compact(result(outfit="gala")), "not json")
    stage = ImagePromptStage(runtime, NoMemories())
    image = ImagePromptRequest(card=card(), request="mandami una foto seduta", preset="illustrious", quality="alta")
    generated = await stage.generate_scene(image, [], "it", outfit_choices(image.card.visual), "job-1")
    assert (generated.fallback, generated.attempts) == (True, 2)
    scene = generated.scene
    assert (scene.framing, scene.outfit, scene.pose) == ("upper body", "usual", ["sitting"])
    assert all(request.grammar == scene_grammar(VOCABULARY, EXAMPLE_OUTFITS) for request in runtime.requests)
    assert "gala" in runtime.requests[1].prompt  # The repair carries the refused output.


@pytest.mark.anyio
async def test_a_valid_scene_is_kept_and_the_fallback_keeps_the_outfit_the_form_picked() -> None:
    image = ImagePromptRequest(card=card(), request="foto", preset="noobai", quality="rapida", outfit="festa")
    stage = ImagePromptStage(ScriptedRuntime(compact(result(outfit="festa"))), NoMemories())
    kept = await stage.generate_scene(image, [], "it", ("festa",), "job-2")
    assert (kept.fallback, kept.attempts, kept.scene.outfit) == (False, 1, "festa")
    failing = ImagePromptStage(ScriptedRuntime("{}", "{}"), NoMemories())
    fallback = await failing.generate_scene(image, [], "it", ("festa",), "job-3")
    assert (fallback.fallback, fallback.scene.outfit) == (True, "festa")


def test_the_prompt_lists_vocabulary_wardrobe_and_closed_values_before_the_data() -> None:
    prompt = render_prompt(
        "gemma4",
        visual=card().visual,
        setting="Genova, oggi",
        recent=[],
        request="mandami una foto al porto",
        language="it",
        outfits=EXAMPLE_OUTFITS,
        vocabulary=VOCABULARY,
        repair=None,
    )
    assert "smile (sorriso, sorridente, sorride)" in prompt
    assert "framing: close-up, portrait, upper body, cowboy shot, full body, wide shot" in prompt
    assert '"key":"festa","descrizione":"Festa"' in prompt
    assert prompt.index("Esempio.") < prompt.index("Richiesta: mandami una foto al porto")
    assert "Messaggi recenti:\nnessuno" in prompt
