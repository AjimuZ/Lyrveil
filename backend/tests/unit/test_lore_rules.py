"""Checks the validation of lore books and entries without a database: T-LOR-04 (unit half) and the stemmed keys."""

import json
from typing import Any

import pytest

from lyrveil.domain.errors import ContentPolicyBlockedError, ValidationFailedError
from lyrveil.domain.lore import KeyStems, exceeds_block_maximum, rendered_line
from lyrveil.memory.retrieval import estimate_tokens
from lyrveil.memory.text import stems
from lyrveil.services.lore_rules import checked_book, checked_entry, checked_keys

MINOR_TEXT = "Una bambina di sette anni abita al faro."


def entry(**fields: Any) -> dict[str, Any]:
    return checked_entry({"content": "Il faro è a nord.", **fields}, "it")


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"name": ""}, id="blank name"),
        pytest.param({"name": "x" * 121}, id="name over 120"),
        pytest.param({"name": "Mondo", "description": "x" * 2001}, id="description over 2000"),
        pytest.param({"name": "Mondo", "scan_depth": 0}, id="scan depth under 1"),
        pytest.param({"name": "Mondo", "scan_depth": 21}, id="scan depth over 20"),
        pytest.param({"name": "Mondo", "scan_depth": True}, id="scan depth is a boolean"),
        pytest.param({"name": "Mondo", "token_budget": 49}, id="token budget under 50"),
        pytest.param({"name": "Mondo", "token_budget": 901}, id="token budget over 900"),
    ],
)
def test_a_book_outside_its_limits_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationFailedError):
        checked_book(fields)


def test_a_book_takes_its_defaults_and_its_limits() -> None:
    assert checked_book({"name": " Mondo "}) == {
        "name": "Mondo",
        "description": "",
        "scan_depth": 4,
        "token_budget": None,
    }
    edge = checked_book({"name": "x" * 120, "description": "x" * 2000, "scan_depth": 20, "token_budget": 900})
    assert (edge["scan_depth"], edge["token_budget"]) == (20, 900)
    assert checked_book({"name": "Mondo", "scan_depth": 1, "token_budget": 50})["token_budget"] == 50


def test_a_world_name_that_describes_a_minor_is_refused_without_quoting_it() -> None:
    with pytest.raises(ContentPolicyBlockedError) as refused:
        checked_book({"name": "Il regno delle bambine"})
    assert "bambine" not in refused.value.detail


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"content": ""}, id="blank content"),
        pytest.param({"content": "x" * 8001}, id="content over 8000"),
        pytest.param({"title": "x" * 81}, id="title over 80"),
        pytest.param({"note": "x" * 501}, id="note over 500"),
        pytest.param({"keys": ["faro"] * 1 + [f"chiave{index}" for index in range(16)]}, id="17 keys"),
        pytest.param({"secondary_keys": [f"chiave{index}" for index in range(17)]}, id="17 secondary keys"),
        pytest.param({"exclude_keys": [f"chiave{index}" for index in range(17)]}, id="17 excluded keys"),
        pytest.param({"keys": ["x" * 61]}, id="key over 60"),
        pytest.param({"keys": "faro"}, id="keys that are not a list"),
        pytest.param({"keys": [3]}, id="a key that is not text"),
        pytest.param({"priority": "alta"}, id="priority that is not a number"),
        pytest.param({"enabled": 1}, id="flag that is not a boolean"),
    ],
)
def test_an_entry_outside_its_limits_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationFailedError):
        entry(**fields)


def test_an_entry_at_its_limits_is_accepted() -> None:
    saved = entry(
        title="x" * 80,
        content="x" * 8000,
        note="x" * 500,
        keys=[f"chiave{index}" for index in range(16)],
        secondary_keys=["y" * 60],
    )
    assert (len(saved["title"]), len(saved["content"]), len(saved["note"])) == (80, 8000, 500)
    assert len(json.loads(saved["keys_json"])) == 16


def test_the_content_guard_reads_titles_and_contents_without_quoting_them() -> None:
    with pytest.raises(ContentPolicyBlockedError) as in_content:
        entry(content=MINOR_TEXT)
    assert "bambina" not in in_content.value.detail
    assert "contenuto" in in_content.value.detail
    with pytest.raises(ContentPolicyBlockedError) as in_title:
        entry(title="La bambina del faro")
    assert "titolo" in in_title.value.detail


def test_a_key_with_no_stem_after_stop_words_is_refused_on_save() -> None:
    assert stems("lui", "it") == []
    with pytest.raises(ValidationFailedError, match="lui"):
        entry(keys=["lui"])
    with pytest.raises(ValidationFailedError):
        entry(secondary_keys=["il"])
    with pytest.raises(ValidationFailedError):
        entry(exclude_keys=["Lui"])


def test_a_stored_key_keeps_its_text_and_its_stems() -> None:
    saved = entry(keys=["  Le Torri di Aldara ", "faro"], secondary_keys=["guerra"], exclude_keys=["Città"])
    assert json.loads(saved["keys_json"]) == ["Le Torri di Aldara", "faro"]
    assert json.loads(saved["secondary_keys_json"]) == ["guerra"]
    assert json.loads(saved["exclude_keys_json"]) == ["Città"]
    stored = KeyStems.from_json(saved["keys_stem_json"])
    assert stored.language == "it"
    assert stored.keys == (tuple(stems("Le Torri di Aldara", "it")), tuple(stems("faro", "it")))
    assert stored.secondary_keys == (tuple(stems("guerra", "it")),)
    assert stored.exclude_keys == (tuple(stems("Città", "it")),)
    assert all(sequence for sequence in stored.keys)


def test_keys_are_stemmed_with_the_language_given() -> None:
    _, italian = checked_keys(["running"], "parole chiave", "it")
    _, english = checked_keys(["running"], "parole chiave", "en")
    assert italian == [tuple(stems("running", "it"))]
    assert english == [tuple(stems("running", "en"))]
    assert english != italian


def test_blank_and_repeated_keys_are_dropped() -> None:
    keys, sequences = checked_keys(["faro", " ", "faro", "porto"], "parole chiave", "it")
    assert keys == ["faro", "porto"]
    assert len(sequences) == 2


def test_an_entry_over_its_block_maximum_is_valid_and_flagged() -> None:
    saved = entry(content="x" * 8000)
    assert saved["content"] == "x" * 8000
    assert exceeds_block_maximum("", "x" * 8000, False, estimate_tokens)
    assert exceeds_block_maximum("", "x" * 8000, True, estimate_tokens)


def test_the_block_maximum_depends_on_the_kind_of_entry() -> None:
    # 2,000 characters estimate to about 670 tokens: over block 11's 600, within block 12's 900.
    content = "x" * 2000
    assert exceeds_block_maximum("", content, True, estimate_tokens)
    assert not exceeds_block_maximum("", content, False, estimate_tokens)
    assert not exceeds_block_maximum("", "Il faro è a nord.", True, estimate_tokens)


def test_the_rendered_line_follows_the_block_template() -> None:
    assert rendered_line("Faro", "È a nord.") == "- Faro: È a nord."
    assert rendered_line("", "È a nord.") == "- È a nord."
