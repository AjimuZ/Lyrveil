"""Checks the tag sanitizer and the packaged tag vocabulary of ADR 0035."""

import pytest

from lyrveil.images.sanitizer import sanitize_tag
from lyrveil.images.tags import CARD_CATEGORIES, VocabularyError, load_vocabulary, parse_vocabulary


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


def test_the_packaged_vocabulary_covers_the_card_categories_with_italian_aliases() -> None:
    vocabulary = load_vocabulary()
    assert {entry.category for entry in vocabulary.entries} == CARD_CATEGORIES
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
    assert vocabulary.contains("hairstyle", "messy hair")
    assert not vocabulary.contains("face", "scar")


def test_a_malformed_vocabulary_is_refused() -> None:
    entry = {"tag": "red hair", "category": "hair_color", "aliases_it": ["rossi"]}
    assert parse_vocabulary({"version": 1, "tags": [entry]}).contains("hair_color", "red hair")
    for document in (
        {"version": 2, "tags": [entry]},
        {"version": 1, "tags": [{**entry, "category": "weather"}]},
        {"version": 1, "tags": [{**entry, "tag": "(red hair)"}]},
        {"version": 1, "tags": [{**entry, "aliases_it": []}, {**entry, "aliases_it": ["x"]}]},
        {"version": 1, "tags": [entry, entry]},
        {"version": 1, "tags": [{"tag": "red hair", "category": "hair_color"}]},
    ):
        with pytest.raises(VocabularyError):
            parse_vocabulary(document)
