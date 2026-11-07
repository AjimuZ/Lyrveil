"""Checks the tag sanitizer, the prompt check of the advanced dialog and the packaged tag vocabulary of ADR 0035
(T-IMG-16). The match of `clip_tokens` with ComfyUI's tokenizer is the docker-level part, in
tests/docker/ImageRuntime.Tests.ps1."""

import pytest

from lyrveil.domain.errors import PromptSyntaxRefusedError
from lyrveil.images.sanitizer import check_user_prompt, prompt_weights, sanitize_tag
from lyrveil.images.tags import (
    CARD_CATEGORIES,
    CATEGORIES,
    SCENE_CATEGORIES,
    VocabularyError,
    load_vocabulary,
    parse_vocabulary,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Red_Hair ", "red hair"),
        ("(masterpiece:1.5)", "masterpiece1.5"),
        ("\\(literal\\)", "literal"),
        ("[a], b; c", "a b c"),
        ("embedding:evil jacket", "jacket"),
        ("a  b   c", "a b c"),
        ("don't stop-me.", "don't stop-me."),
        ("(:)", ""),
    ],
)
def test_the_sanitizer_keeps_only_safe_characters(raw: str, expected: str) -> None:
    assert sanitize_tag(raw) == expected
    assert sanitize_tag(expected) == expected


def test_the_packaged_vocabulary_covers_the_card_and_scene_categories_with_italian_aliases() -> None:
    vocabulary = load_vocabulary()
    assert {entry.category for entry in vocabulary.entries} == CARD_CATEGORIES | SCENE_CATEGORIES == CATEGORIES
    assert vocabulary.contains("expression", "smile")
    assert vocabulary.contains("pose", "sitting")
    assert vocabulary.contains("lighting", "warm lighting")
    assert vocabulary.categories_of("capelli biondi") == {"hair_color"}
    assert vocabulary.categories_of("tall building") == frozenset()
    assert vocabulary.clip_tokens("hair over one eye") == 4
    assert vocabulary.ordered("skin") == ("pale skin", "tan", "dark skin")
    assert vocabulary.tags("hair_length") == {
        "very short hair",
        "short hair",
        "medium hair",
        "long hair",
        "very long hair",
    }
    assert vocabulary.tags("skin") == {"pale skin", "tan", "dark skin"}
    assert all(entry.aliases_it for entry in vocabulary.entries)
    assert all(sanitize_tag(entry.tag) == entry.tag for entry in vocabulary.entries)
    assert all(entry.clip_tokens >= len(entry.tag.split()) for entry in vocabulary.entries)
    assert vocabulary.contains("hairstyle", "messy hair")
    assert not vocabulary.contains("face", "scar")


def test_a_malformed_vocabulary_is_refused() -> None:
    entry = {"tag": "red hair", "category": "hair_color", "aliases_it": ["rossi"], "clip_tokens": 2}
    assert parse_vocabulary({"version": 1, "tags": [entry]}).contains("hair_color", "red hair")
    for document in (
        {"version": 2, "tags": [entry]},
        {"version": 1, "tags": [{**entry, "category": "weather"}]},
        {"version": 1, "tags": [{**entry, "tag": "(red hair)"}]},
        {"version": 1, "tags": [{**entry, "aliases_it": []}, {**entry, "aliases_it": ["x"]}]},
        {"version": 1, "tags": [entry, entry]},
        {"version": 1, "tags": [{"tag": "red hair", "category": "hair_color", "clip_tokens": 2}]},
        {"version": 1, "tags": [{key: value for key, value in entry.items() if key != "clip_tokens"}]},
        {"version": 1, "tags": [{**entry, "clip_tokens": 1}]},
        {"version": 1, "tags": [{**entry, "clip_tokens": True}]},
        {"version": 1, "tags": [{**entry, "clip_tokens": 2.0}]},
    ):
        with pytest.raises(VocabularyError):
            parse_vocabulary(document)


def test_prompt_weights_follow_comfyui_rules() -> None:
    segments = dict(prompt_weights("a, (b), ((c)), (d:1.3), ((e:0.6) f), \\(g\\)"))
    assert segments["a, "] == 1.0
    assert segments["b"] == pytest.approx(1.1)
    assert segments["c"] == pytest.approx(1.21)
    assert segments["d"] == 1.3
    assert segments["e"] == 0.6
    assert segments[" f"] == pytest.approx(1.1)
    assert segments[", g"] == 1.0


@pytest.mark.parametrize(
    "text",
    ["masterpiece, (red hair:1.5), (smile:0.5)", "((((freckles))))", "\\(not a weight:9\\)", "(a: b)", "plain text"],
)
def test_the_advanced_dialog_accepts_weights_from_half_to_one_and_a_half(text: str) -> None:
    check_user_prompt(text)


@pytest.mark.parametrize(
    "text",
    [
        "embedding:evil",
        "red hair, (Embedding:evil:1.0)",
        "(red hair:1.6)",
        "(smile:0.4)",
        "(((((freckles)))))",
        "((a) (b:1.6))",
        "(x:nan)",
        "(x:inf)",
        "(unclosed",
        "closed) (twice",
        "\\(escaped)",
    ],
)
def test_the_advanced_dialog_refuses_embeddings_bad_weights_and_bare_parentheses(text: str) -> None:
    with pytest.raises(PromptSyntaxRefusedError) as refused:
        check_user_prompt(text)
    assert refused.value.code == "PROMPT_SYNTAX_REFUSED"
    assert refused.value.status == 422
