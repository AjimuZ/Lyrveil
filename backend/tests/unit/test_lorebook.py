"""Checks the pure conversion between lore books and `character_book` or `lorebook_v3` (P12.4): T-LOR-05 and T-LOR-08,
unit halves."""

import json
from typing import Any

import pytest

from lyrveil.domain.errors import ContentPolicyBlockedError, ImportInvalidError
from lyrveil.domain.lore import BookKind, BookStatus, KeyStems, LoreBook, LoreEntry
from lyrveil.services.lorebook import (
    ImportedBook,
    convert_book,
    convert_entry,
    export_book,
    parse_lorebook,
    split_decorators,
)


def entry_of(imported: ImportedBook, index: int) -> LoreEntry:
    """The stored entry an imported one becomes, as lyrveil.persistence.lore reads it back."""
    item = imported.entries[index]
    columns = item.columns
    return LoreEntry(
        id=f"e{index}",
        book_id="b",
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
        source=json.loads(json.dumps(item.source)),
    )


def book_of(imported: ImportedBook) -> LoreBook:
    return LoreBook(
        id="b",
        kind=BookKind.WORLD,
        character_id=None,
        status=BookStatus.ACTIVE,
        entry_count=len(imported.entries),
        source=json.loads(json.dumps(imported.source)),
        created_at="",
        updated_at="",
        **imported.columns,
    )


def raw_entry(**fields: Any) -> dict[str, Any]:
    return {"keys": ["faro"], "content": "Il faro è spento.", "extensions": {}, "enabled": True, **fields}


def test_the_four_decorators_are_applied_and_the_others_are_kept_in_order() -> None:
    found = split_decorators(
        "@@activate\n@@depth 4\n@@@role system\n@@additional_keys torre, lanterna\n@@exclude_keys tempesta\n"
        "@@dont_activate\n@@@fallback\nIl faro.\n@@not_a_decorator"
    )
    assert (found.constant, found.disabled) == (True, True)
    assert (found.additional_keys, found.exclude_keys) == (["torre", "lanterna"], ["tempesta"])
    # The fallback of a kept decorator stays with it; the fallback of an applied one is dropped.
    assert found.kept == ["@@depth 4", "@@@role system"]
    assert found.content == "Il faro.\n@@not_a_decorator"


def test_an_entry_maps_its_fields_and_keeps_what_lyrveil_does_not_use() -> None:
    raw = raw_entry(
        name="Il faro",
        comment="Nota",
        priority=5,
        insertion_order=10,
        case_sensitive=True,
        selective=True,
        secondary_keys=["notte"],
        id=7,
        position="after_char",
        use_regex=False,
        unknown=[1, 2],
        content="@@depth 4\n@@additional_keys torre\n@@exclude_keys tempesta\nIl faro è spento.",
    )
    entry, report = convert_entry(raw, 0, "it")
    assert entry is not None
    assert report == []
    columns = entry.columns
    assert (columns["title"], columns["note"], columns["content"]) == ("Il faro", "Nota", "Il faro è spento.")
    assert json.loads(columns["keys_json"]) == ["faro", "torre"]
    assert json.loads(columns["secondary_keys_json"]) == ["notte"]
    assert json.loads(columns["exclude_keys_json"]) == ["tempesta"]
    assert (columns["priority"], columns["insertion_order"], columns["case_sensitive"]) == (5, 10, True)
    assert (columns["constant"], columns["enabled"]) == (False, True)
    assert entry.source == {
        "file_index": 0,
        "fields": {"extensions": {}, "id": 7, "position": "after_char", "use_regex": False, "unknown": [1, 2]},
        "decorators": ["@@depth 4"],
        "selective": True,
    }


def test_secondary_keys_apply_only_when_selective() -> None:
    entry, _ = convert_entry(raw_entry(secondary_keys=["notte"]), 0, "it")
    assert entry is not None
    assert json.loads(entry.columns["secondary_keys_json"]) == []
    assert entry.source["secondary_keys"] == ["notte"]


def test_regex_entries_and_entries_without_a_usable_key_are_disabled_and_reported() -> None:
    regex, regex_report = convert_entry(raw_entry(use_regex=True, keys=["faro"]), 0, "it")
    assert regex is not None
    assert regex.columns["enabled"] is False
    assert [item.reason for item in regex_report] == ["use_regex"]

    keyless, keyless_report = convert_entry(raw_entry(keys=["lui"], name="Senza"), 1, "it")
    assert keyless is not None
    assert (keyless.columns["enabled"], json.loads(keyless.columns["keys_json"])) == (False, [])
    assert [(item.reason, item.keys) for item in keyless_report] == [
        ("keys_left_out", ("lui",)),
        ("no_usable_key", ()),
    ]
    assert keyless_report[1].as_json()["title"] == "Senza"

    constant, constant_report = convert_entry(raw_entry(keys=[], content="@@activate\nSempre."), 2, "it")
    assert constant is not None
    assert (constant.columns["enabled"], constant.columns["constant"], constant_report) == (True, True, [])


def test_entries_that_cannot_be_stored_are_left_out_and_reported() -> None:
    imported = convert_book(
        {
            "name": "Aldara",
            "entries": [
                raw_entry(content="@@depth 2\n  "),
                raw_entry(content="x" * 8001),
                raw_entry(name="t" * 81),
            ],
        },
        "it",
        "Mondo",
    )
    assert len(imported.entries) == 1
    assert imported.entries[0].columns["title"] == "t" * 80
    assert [(item.index, item.reason) for item in imported.report] == [
        (0, "empty_content"),
        (1, "content_too_long"),
        (2, "title_cut"),
    ]


def test_book_values_out_of_range_take_the_defaults_and_return_on_export() -> None:
    imported = convert_book({"name": "  ", "scan_depth": 50, "token_budget": 2048, "entries": []}, "it", "Alba")
    assert imported.columns == {"name": "Alba", "description": "", "scan_depth": 4, "token_budget": None}
    exported = export_book(book_of(imported), [])
    assert (exported["scan_depth"], exported["token_budget"]) == (50, 2048)


def test_a_round_trip_keeps_every_field_and_the_decorators_that_were_not_applied() -> None:
    """T-LOR-08, unit half."""
    raw: dict[str, Any] = {
        "name": "Aldara",
        "description": "Un regno di mare.",
        "scan_depth": 6,
        "token_budget": 2048,
        "recursive_scanning": True,
        "extensions": {"tool": {"x": 1}},
        "entries": [
            raw_entry(
                content="@@depth 4\n@@@role system\nIl faro è spento.",
                name="Il faro",
                comment="Nota",
                priority=5,
                insertion_order=10,
                case_sensitive=False,
                constant=False,
                use_regex=False,
                selective=True,
                secondary_keys=["notte"],
                id=7,
                position="after_char",
            ),
            raw_entry(keys=["/re(gno)?/"], use_regex=True, insertion_order=20, id="b"),
            raw_entry(keys=["lui", "faro"], insertion_order=30, selective=False, secondary_keys=["nave"]),
            raw_entry(keys=["lui"], insertion_order=40, priority=1.5),
        ],
    }
    imported = convert_book(raw, "it", "Mondo")
    exported = export_book(book_of(imported), [entry_of(imported, index) for index in range(len(imported.entries))])
    for name, value in raw.items():
        if name != "entries":
            assert exported[name] == value, name
    assert len(exported["entries"]) == len(raw["entries"])
    for original, written in zip(raw["entries"], exported["entries"], strict=True):
        for name, value in original.items():
            assert written[name] == value, name


def test_applied_decorators_become_fields_and_exclude_keys_return_as_a_line() -> None:
    imported = convert_book(
        {"entries": [raw_entry(content="@@activate\n@@exclude_keys tempesta\n@@depth 2\nTesto.")]}, "it", "Mondo"
    )
    written = export_book(book_of(imported), [entry_of(imported, 0)])["entries"][0]
    assert written["constant"] is True
    assert written["content"] == "@@depth 2\n@@exclude_keys tempesta\nTesto."


def lorebook_bytes(data: object, spec: str = "lorebook_v3") -> bytes:
    return json.dumps({"spec": spec, "data": data}).encode("utf-8")


def test_a_file_that_is_not_a_lorebook_is_refused() -> None:
    with pytest.raises(ImportInvalidError):
        parse_lorebook(b"not json")
    with pytest.raises(ImportInvalidError, match="lorebook_v3"):
        parse_lorebook(lorebook_bytes({"entries": []}, spec="chara_card_v3"))
    with pytest.raises(ImportInvalidError, match=r"data\.entries\[0\]\.keys"):
        parse_lorebook(lorebook_bytes({"entries": [{"keys": "faro", "content": "x"}]}))
    with pytest.raises(ContentPolicyBlockedError, match=r"data\.entries\[0\]\.content"):
        parse_lorebook(lorebook_bytes({"entries": [raw_entry(content="Una bambina di sette anni abita al faro.")]}))
    assert parse_lorebook(lorebook_bytes({"name": "Aldara", "entries": []})) == {"name": "Aldara", "entries": []}
