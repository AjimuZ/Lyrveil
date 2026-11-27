"""Checks the keyword matcher of ADR 0049 without a database: T-LOR-01."""

import json
from typing import Any

import pytest

from lyrveil.domain.errors import ValidationFailedError
from lyrveil.domain.lore import KeyStems, LoreEntry
from lyrveil.services.lore_matcher import MatchReason, ScanWindow, match_entries, match_entry
from lyrveil.services.lore_rules import checked_entry, checked_keys


def make_entry(language: str = "it", **fields: Any) -> LoreEntry:
    """Returns an entry as the service would store it: keys stemmed for `language` by the save rules."""
    columns = checked_entry({"content": "Testo.", **fields}, language)
    return LoreEntry(
        id="entry",
        book_id="book",
        title=columns["title"],
        content=columns["content"],
        keys=tuple(json.loads(columns["keys_json"])),
        secondary_keys=tuple(json.loads(columns["secondary_keys_json"])),
        exclude_keys=tuple(json.loads(columns["exclude_keys_json"])),
        stems=KeyStems.from_json(columns["keys_stem_json"]),
        case_sensitive=columns["case_sensitive"],
        constant=columns["constant"],
        enabled=columns["enabled"],
        priority=columns["priority"],
        insertion_order=columns["insertion_order"],
        note=columns["note"],
        never_inserted=False,
    )


def outcome(entry: LoreEntry, *messages: str, depth: int = 4, language: str = "it") -> Any:
    return match_entries([entry], list(messages), depth, language)[0]


def test_italian_key_matches_another_word_form() -> None:
    result = outcome(make_entry(keys=["lupo"]), "Ho sentito i lupi ululare.")

    assert result.active is True
    assert result.reason is MatchReason.MATCHED
    assert result.matched_key == "lupo"


def test_english_key_matches_another_word_form() -> None:
    entry = make_entry("en", keys=["castle"])

    assert outcome(entry, "Old castles stand there.", language="en").matched_key == "castle"


def test_stem_sequence_must_be_contiguous_and_in_order() -> None:
    entry = make_entry("en", keys=["dark forest"])

    assert outcome(entry, "She entered the dark forests.", language="en").active is True
    assert outcome(entry, "The forest was dark.", language="en").reason is MatchReason.NO_KEY_MATCHED
    assert outcome(entry, "A dark old forest.", language="en").reason is MatchReason.NO_KEY_MATCHED


def test_key_never_matches_across_two_messages() -> None:
    entry = make_entry("en", keys=["dark forest"])

    assert outcome(entry, "It was dark", "forest ahead", language="en").active is False


def test_case_sensitive_key_needs_exact_text_at_word_boundaries() -> None:
    entry = make_entry(keys=["Aldara"], case_sensitive=True)

    assert outcome(entry, "Siamo arrivati ad Aldara.").active is True
    assert outcome(entry, "Siamo arrivati ad aldara.").active is False
    assert outcome(entry, "Gli Aldaranti partirono.").active is False
    assert outcome(entry, "(Aldara)").active is True


def test_case_sensitive_key_with_punctuation_at_its_edge() -> None:
    entry = make_entry(keys=["Aldara!"], case_sensitive=True)

    assert outcome(entry, "Viva Aldara!! Sempre.").active is True
    assert outcome(entry, "Viva aldara!! Sempre.").active is False
    assert outcome(entry, "Viva Aldara.").active is False


def test_secondary_key_must_also_match() -> None:
    entry = make_entry(keys=["faro"], secondary_keys=["tempesta", "nebbia"])

    both = outcome(entry, "Il faro nella nebbia.")
    assert (both.active, both.matched_key, both.matched_secondary_key) == (True, "faro", "nebbia")
    alone = outcome(entry, "Il faro è spento.")
    assert (alone.active, alone.reason) == (False, MatchReason.SECONDARY_NOT_MATCHED)
    assert outcome(entry, "Nebbia fitta.").reason is MatchReason.NO_KEY_MATCHED


def test_exclude_key_deactivates_and_is_named() -> None:
    entry = make_entry(keys=["faro"], exclude_keys=["sogno"])

    result = outcome(entry, "Nel mio sogno c'era un faro.")

    assert (result.active, result.reason, result.excluded_key) == (False, MatchReason.EXCLUDED, "sogno")
    assert outcome(entry, "C'era un faro.").active is True


def test_disabled_entry_is_never_active_even_when_constant() -> None:
    assert outcome(make_entry(keys=["faro"], enabled=False), "Il faro.").reason is MatchReason.DISABLED
    assert outcome(make_entry(constant=True, enabled=False), "Il faro.").reason is MatchReason.DISABLED


def test_constant_entry_is_active_without_keys_and_ignores_exclude_keys() -> None:
    entry = make_entry(constant=True, exclude_keys=["sogno"])

    result = outcome(entry, "Nel mio sogno.")

    assert (result.active, result.reason, result.matched_key) == (True, MatchReason.CONSTANT, None)
    assert outcome(entry).active is True


def test_entry_with_no_keys_stays_inactive() -> None:
    assert outcome(make_entry(), "Il faro.").reason is MatchReason.NO_KEYS


def test_scan_depth_reads_only_the_newest_messages() -> None:
    entry = make_entry(keys=["faro"])
    messages = ["Parliamo del faro.", "Va bene.", "Certo."]

    assert outcome(entry, *messages, depth=2).active is False
    assert outcome(entry, *messages, depth=3).active is True
    assert outcome(entry, *messages, depth=0).active is False
    assert outcome(entry, *messages, depth=20).active is True


def test_keys_saved_in_another_language_are_stemmed_again() -> None:
    entry = make_entry("it", keys=["castle"])
    assert entry.stems.language == "it"

    assert outcome(entry, "Two castles.", language="en").active is True


def test_window_keeps_one_stem_sequence_per_message() -> None:
    window = ScanWindow.of(["faro", "mare", "sole"], 2, "it")

    assert window.texts == ("mare", "sole")
    assert len(window.stem_sequences) == 2
    assert match_entry(make_entry(keys=["faro"]), window).active is False
    assert match_entry(make_entry(keys=["sole"]), window).active is True


def test_key_with_no_stem_is_refused_on_save() -> None:
    with pytest.raises(ValidationFailedError, match="lui"):
        checked_keys(["lui"], "parole chiave", "it")
    with pytest.raises(ValidationFailedError):
        make_entry(keys=["lui"])
