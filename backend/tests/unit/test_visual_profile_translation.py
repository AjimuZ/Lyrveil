"""Structured profile translation without a model: the grammar of VisualIdentityResult takes its closed values from
the tag vocabulary and the card's outfit keys, the worked example passes it, and the merge keeps locked slots."""

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from lyrveil.domain.card import CharacterCard
from lyrveil.images.tags import load_vocabulary
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.prompts.structured import OutputRejectedError, StructuredOutputInvalidError
from lyrveil.prompts.visual_profile import (
    EXAMPLE_RESULT,
    EXAMPLE_VISUAL,
    VisualIdentityResult,
    identity_grammar,
    render_prompt,
)
from lyrveil.services.card_rules import validate_card
from lyrveil.services.visual_profile_translation import apply_identity_result, translate_visual_profile
from unit.test_character_draft import matches

VOCABULARY = load_vocabulary()
GRAMMAR = identity_grammar(VOCABULARY, ["giorno_libero"])


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def result(**fields: Any) -> dict[str, Any]:
    return {**EXAMPLE_RESULT, **fields}


def example_card(**visual: Any) -> CharacterCard:
    return validate_card({"identity": {"first_name": "Dario", "age": 34}, "visual": {**EXAMPLE_VISUAL, **visual}})


class ScriptedRuntime:
    """A TextRuntime whose `generate` returns the scripted outputs in turn and records each request."""

    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.requests: list[TextRequest] = []

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split())

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        yield TextChunk("", "stop", "")

    async def generate(self, request: TextRequest) -> str:
        self.requests.append(request)
        return self.outputs.pop(0)

    async def abort(self, genkey: str) -> bool:
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=8192)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_the_grammar_accepts_the_worked_example_and_every_vocabulary_value() -> None:
    assert matches(GRAMMAR, compact(EXAMPLE_RESULT))
    for tag in VOCABULARY.ordered("hair_color"):
        assert matches(GRAMMAR, compact(result(hair_color=tag)))
    assert matches(GRAMMAR, compact(result(skin=None, outfits=[])))


@pytest.mark.parametrize(
    "refused",
    [
        result(hair_color="plaid hair"),
        result(eye_color="red hair"),  # A vocabulary tag of another category.
        result(face=["freckles", "dimples", "beard", "goatee", "wrinkles"]),
        result(style=["(masterpiece:1.5)"]),
        result(outfits=[{"key": "gala", "tags_en": ["gown"]}]),
        result(outfits=[{"key": "giorno_libero", "tags_en": []}, {"key": "giorno_libero", "tags_en": []}]),
        {key: value for key, value in EXAMPLE_RESULT.items() if key != "extra"},
    ],
)
def test_the_grammar_refuses_values_outside_the_vocabulary_and_the_card(refused: dict[str, Any]) -> None:
    assert not matches(GRAMMAR, compact(refused))


def test_the_grammar_is_deterministic_and_closed_without_named_outfits() -> None:
    assert identity_grammar(VOCABULARY, ["giorno_libero"]) == GRAMMAR
    bare = identity_grammar(VOCABULARY, [])
    assert matches(bare, compact(result(outfits=[])))
    assert not matches(bare, compact(EXAMPLE_RESULT))


def test_the_worked_example_is_a_valid_result_and_card() -> None:
    card = example_card()
    merged = apply_identity_result(card.visual, VisualIdentityResult.model_validate(EXAMPLE_RESULT))
    stored = validate_card(card.model_copy(update={"visual": merged}).model_dump(mode="json"))
    assert stored.visual.identity_en.hair_color == "brown hair"
    assert stored.visual.outfits[0].tags_en == ["grey sweater", "jeans"]


def test_the_merge_keeps_locked_slots_and_outfits_the_result_leaves_out() -> None:
    card = example_card(
        identity_en={"hair_color": "black hair", "hair_length": "long hair", "hairstyle": ["braid"], "skin": "tan"},
        usual_outfit_en=["white shirt"],
        locked_fields=["hair", "clothing", "face"],
        outfits=[{"key": "giorno_libero", "label": "Giorno libero", "tags_en": ["old coat"]}],
    )
    merged = apply_identity_result(card.visual, VisualIdentityResult.model_validate(result(outfits=[])))
    identity = merged.identity_en
    assert (identity.hair_color, identity.hair_length, identity.hairstyle) == ("black hair", "long hair", ["braid"])
    assert (identity.skin, identity.face) == ("tan", [])
    assert (identity.eye_color, identity.body) == ("brown eyes", ["tall", "broad shoulders"])
    assert merged.usual_outfit_en == ["white shirt"]
    assert merged.outfits[0].tags_en == ["old coat"]
    assert merged.locked_fields == ["hair", "clothing", "face"]
    twice = VisualIdentityResult.model_validate(result(outfits=[{"key": "giorno_libero", "tags_en": []}] * 2))
    with pytest.raises(OutputRejectedError):
        apply_identity_result(card.visual, twice)


@pytest.mark.anyio
async def test_translation_runs_under_the_vocabulary_grammar_and_repairs_once() -> None:
    card = example_card(locked_fields=["eyes"], identity_en={"eye_color": "grey eyes"})
    runtime = ScriptedRuntime(compact(result(hair_color="plaid hair")), compact(EXAMPLE_RESULT))
    translated = await translate_visual_profile(runtime, card, template="chatml", genkey="job-1")
    assert translated.attempts == 2
    assert translated.card.visual.identity_en.hair_color == "brown hair"
    assert translated.card.visual.identity_en.eye_color == "grey eyes"  # Locked.
    assert all(request.grammar == GRAMMAR for request in runtime.requests)
    assert "plaid hair" in runtime.requests[1].prompt  # The repair carries the refused output.
    assert "hair_color" in runtime.requests[1].prompt


@pytest.mark.anyio
async def test_translation_fails_after_the_repair_when_a_free_tag_is_too_long() -> None:
    long_tags = result(extra=["one two three four five"])
    runtime = ScriptedRuntime(compact(long_tags), compact(long_tags))
    with pytest.raises(StructuredOutputInvalidError) as invalid:
        await translate_visual_profile(runtime, example_card(), template="gemma4", genkey="job-2")
    assert "extra" in invalid.value.detail


def test_the_prompt_lists_the_closed_values_with_their_italian_words_and_ends_with_the_card() -> None:
    prompt = render_prompt("gemma4", example_card().visual, VOCABULARY, None)
    assert "red hair (capelli rossi, rosso, rossi)" in prompt
    assert prompt.index("Esempio.") < prompt.rindex("Aspetto del personaggio:")
    assert "Rispondi solo con il JSON richiesto." in prompt
