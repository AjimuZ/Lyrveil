"""Converts between Lyrveil lore books and the books of Character Cards: a card's `character_book` and a standalone
`lorebook_v3` file (ADR 0049, Import and export). Pure: it reads no database, so the save of a card import, the world
import, both exports and migration 0023 share it.

Import maps `name` to `title`, `comment` to `note`, and applies `secondary_keys` only when `selective` is true. The
decorators `@@activate`, `@@dont_activate`, `@@additional_keys` and `@@exclude_keys` are applied and leave the content;
every other leading `@@` line is kept. Entries with `use_regex` true, and entries left with no usable key that are not
constant, are imported disabled. Whatever Lyrveil does not use is kept in `source_json`, and export writes it back, so
a file read and written again keeps every field.
"""

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import ValidationError

from lyrveil.consistency.content_guard import check_texts, find_violation
from lyrveil.domain.errors import (
    ContentPolicyBlockedError,
    ImportInvalidError,
    PayloadTooLargeError,
    ValidationFailedError,
)
from lyrveil.domain.exchange import MAX_IMPORT_BYTES
from lyrveil.domain.lore import (
    DEFAULT_INSERTION_ORDER,
    DEFAULT_PRIORITY,
    DEFAULT_SCAN_DEPTH,
    MAX_BOOK_NAME_CHARS,
    MAX_CONTENT_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_ENTRIES_PER_BOOK,
    MAX_KEY_CHARS,
    MAX_KEYS_PER_LIST,
    MAX_NOTE_CHARS,
    MAX_ORDER_MAGNITUDE,
    MAX_SCAN_DEPTH,
    MAX_TITLE_CHARS,
    MAX_TOKEN_BUDGET,
    MIN_SCAN_DEPTH,
    MIN_TOKEN_BUDGET,
    LoreBook,
    LoreEntry,
)
from lyrveil.domain.tavern_card import MAX_JSON_DEPTH, CharacterBook, _card_texts, _depth_over, _join_path
from lyrveil.memory.text import stems
from lyrveil.services.lore_rules import checked_entry

SPEC_LOREBOOK_V3 = "lorebook_v3"
# The name of an imported book whose own name is blank or refused: a world needs one, and an own book takes the
# character's first name instead.
DEFAULT_WORLD_NAME = "Lorebook importato"

# The entry fields Lyrveil maps to its own columns. Every other field of an entry is kept verbatim in `source_json`.
_MAPPED_ENTRY_FIELDS = frozenset(
    {
        "keys",
        "secondary_keys",
        "content",
        "name",
        "comment",
        "enabled",
        "insertion_order",
        "case_sensitive",
        "constant",
        "priority",
        "selective",
    }
)
_MAPPED_BOOK_FIELDS = frozenset({"name", "description", "scan_depth", "token_budget", "entries"})

REPORT_DETAILS = {
    "use_regex": "La voce usa espressioni regolari, che Lyrveil non legge: è importata disattivata.",
    "no_usable_key": "La voce non ha parole chiave utilizzabili e non è sempre attiva: è importata disattivata.",
    "keys_left_out": (
        f"Alcune parole chiave sono troppo comuni, superano {MAX_KEY_CHARS} caratteri o sono oltre le prime "
        f"{MAX_KEYS_PER_LIST}: restano solo nel file e tornano nell'esportazione."
    ),
    "title_cut": f"Il nome della voce supera {MAX_TITLE_CHARS} caratteri ed è stato accorciato.",
    "note_cut": f"Il commento supera {MAX_NOTE_CHARS} caratteri ed è stato accorciato.",
    "empty_content": "La voce non ha contenuto: non è importata.",
    "content_too_long": f"Il contenuto supera {MAX_CONTENT_CHARS} caratteri: la voce non è importata.",
    "refused": "La voce non rispetta le regole delle voci di lore: non è importata.",
    "too_many_entries": (
        f"Un libro può avere al massimo {MAX_ENTRIES_PER_BOOK} voci: le successive non sono importate."
    ),
}


@dataclass(frozen=True)
class ReportItem:
    """One finding of an import. `index` is the entry's 0-based place in the file's `entries`, `title` its name as
    imported, `reason` a REPORT_DETAILS key and `keys` the keys it concerns (the left-out ones for `keys_left_out`)."""

    index: int
    title: str
    reason: str
    keys: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {**asdict(self), "keys": list(self.keys), "detail": REPORT_DETAILS[self.reason]}


@dataclass(frozen=True)
class ImportedEntry:
    """An entry ready to insert: `columns` are what `checked_entry` returns, `source` what goes in `source_json`."""

    columns: dict[str, Any]
    source: dict[str, Any]


@dataclass(frozen=True)
class ImportedBook:
    """A converted book: `columns` holds `name`, `description`, `scan_depth` and `token_budget`, `source` the book's
    `source_json`, `entries` the entries to insert in file order, and `report` what the import changed or left out."""

    columns: dict[str, Any]
    source: dict[str, Any]
    entries: list[ImportedEntry]
    report: list[ReportItem] = field(default_factory=list)

    def report_json(self) -> dict[str, Any]:
        """Returns the import report: how many entries are imported, how many of them are disabled, and the items."""
        return {
            "entries": len(self.entries),
            "disabled": sum(1 for entry in self.entries if not entry.columns["enabled"]),
            "items": [item.as_json() for item in self.report],
        }


@dataclass(frozen=True)
class Decorators:
    """The leading `@@` lines of an entry's content. `constant`, `disabled`, `additional_keys` and `exclude_keys` are
    what the four applied decorators say; `kept` holds every other line verbatim, in order; `content` is the rest."""

    constant: bool
    disabled: bool
    additional_keys: list[str]
    exclude_keys: list[str]
    kept: list[str]
    content: str


def _comma_list(argument: str) -> list[str]:
    return [part.strip() for part in argument.split(",") if part.strip()]


def split_decorators(content: str) -> Decorators:
    """Returns the decorators at the start of `content`, which has `\\n` line ends: each line that starts with `@@`,
    up to the first line that does not. A `@@@` line is the fallback of the decorator above it, read only when that one
    is not supported: it is kept after a kept decorator and dropped after an applied one, whose meaning it cannot
    change. Decorator names are compared as written, lower case in the specification."""
    lines = content.split("\n")
    constant = disabled = False
    additional: list[str] = []
    exclude: list[str] = []
    kept: list[str] = []
    applied_last = False
    index = 0
    while index < len(lines) and lines[index].startswith("@@"):
        line = lines[index]
        index += 1
        if line.startswith("@@@"):
            if not applied_last:
                kept.append(line)
            continue
        name, _, argument = line[2:].strip().partition(" ")
        applied_last = True
        if name == "activate":
            constant = True
        elif name == "dont_activate":
            disabled = True
        elif name == "additional_keys":
            additional.extend(_comma_list(argument))
        elif name == "exclude_keys":
            exclude.extend(_comma_list(argument))
        else:
            applied_last = False
            kept.append(line)
    return Decorators(constant, disabled, additional, exclude, kept, "\n".join(lines[index:]))


def _text(raw: Mapping[str, Any], name: str) -> str:
    value = raw.get(name)
    return value if isinstance(value, str) else ""


def _texts(raw: Mapping[str, Any], name: str) -> list[str]:
    value = raw.get(name)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _flag(raw: Mapping[str, Any], name: str, default: bool) -> bool:
    value = raw.get(name)
    return value if isinstance(value, bool) else default


def _integer(raw: Mapping[str, Any], name: str, low: int, high: int) -> int | None:
    """Returns the field as an integer in [low, high], a whole float included, or None for a missing or other value."""
    value = raw.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return None
    number = int(value)
    return number if low <= number <= high else None


def _cut(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit].rstrip()


def _usable_keys(raw: Sequence[str], language: str | None) -> tuple[list[str], list[str], list[str]]:
    """Returns the keys, trimmed and without blanks or repeats, split into those an entry can store and those it
    cannot (a key over 60 characters, with no stem left after stop-word removal, or after the 16th usable one), and
    the whole cleaned list in file order."""
    usable: list[str] = []
    left_out: list[str] = []
    cleaned = list(dict.fromkeys(item.strip() for item in raw if item.strip()))
    for key in cleaned:
        if len(usable) < MAX_KEYS_PER_LIST and len(key) <= MAX_KEY_CHARS and stems(key, language):
            usable.append(key)
        else:
            left_out.append(key)
    return usable, left_out, cleaned


def convert_entry(
    raw: Mapping[str, Any], index: int, language: str | None
) -> tuple[ImportedEntry | None, list[ReportItem]]:
    """Returns the entry that the file's entry `raw` becomes, or None when it is not imported, with its report items.

    `raw` passed the CharacterBook model, so its key lists and texts are lists of text and text, or None. The keys are
    stemmed for `language`. An entry with no content, or more than 8,000 characters of it after the decorators, is not
    imported, and neither is one that `checked_entry` still refuses. Never raises.
    """
    report: list[ReportItem] = []
    content = _text(raw, "content").replace("\r\n", "\n").replace("\r", "\n")
    decorators = split_decorators(content)
    raw_name = _text(raw, "name").strip()
    title = _cut(raw_name, MAX_TITLE_CHARS)
    source: dict[str, Any] = {
        "file_index": index,
        "fields": {name: value for name, value in raw.items() if name not in _MAPPED_ENTRY_FIELDS},
    }
    if decorators.kept:
        source["decorators"] = decorators.kept
    if "selective" in raw:
        source["selective"] = raw["selective"]
    if title != raw_name:
        source["name"] = raw_name
        report.append(ReportItem(index, title, "title_cut"))
    raw_note = _text(raw, "comment").strip()
    note = _cut(raw_note, MAX_NOTE_CHARS)
    if note != raw_note:
        source["comment"] = raw_note
        report.append(ReportItem(index, title, "note_cut"))

    selective = raw.get("selective") is True
    secondary_raw = _texts(raw, "secondary_keys")
    if secondary_raw and not selective:
        source["secondary_keys"] = secondary_raw
    lists = {
        "keys": _usable_keys([*_texts(raw, "keys"), *decorators.additional_keys], language),
        "secondary_keys": _usable_keys(secondary_raw if selective else [], language),
        "exclude_keys": _usable_keys(decorators.exclude_keys, language),
    }
    left_out = {name: found[1] for name, found in lists.items() if found[1]}
    if left_out:
        source["left_out_keys"] = left_out
        # The whole list in file order, so an export of an unedited list writes it back in the same order.
        source["file_keys"] = {name: lists[name][2] for name in left_out}
        dropped = tuple(key for keys in left_out.values() for key in keys)
        report.append(ReportItem(index, title, "keys_left_out", dropped))

    constant = _flag(raw, "constant", False) or decorators.constant
    enabled = _flag(raw, "enabled", True) and not decorators.disabled
    if raw.get("use_regex") is True:
        report.append(ReportItem(index, title, "use_regex"))
        if enabled:
            source["disabled_on_import"] = "use_regex"
            enabled = False
    if not lists["keys"][0] and not constant:
        report.append(ReportItem(index, title, "no_usable_key"))
        if enabled:
            source["disabled_on_import"] = "no_usable_key"
            enabled = False

    numbers: dict[str, int] = {}
    for name, default in (("priority", DEFAULT_PRIORITY), ("insertion_order", DEFAULT_INSERTION_ORDER)):
        value = _integer(raw, name, -MAX_ORDER_MAGNITUDE, MAX_ORDER_MAGNITUDE)
        if value is None and name in raw:
            source[name] = raw[name]
        numbers[name] = default if value is None else value

    body = decorators.content.strip()
    if not body:
        return None, [*report, ReportItem(index, title, "empty_content")]
    if len(body) > MAX_CONTENT_CHARS:
        return None, [*report, ReportItem(index, title, "content_too_long")]
    fields = {
        "title": title,
        "content": body,
        "keys": lists["keys"][0],
        "secondary_keys": lists["secondary_keys"][0],
        "exclude_keys": lists["exclude_keys"][0],
        "case_sensitive": _flag(raw, "case_sensitive", False),
        "constant": constant,
        "enabled": enabled,
        "note": note,
        **numbers,
    }
    try:
        columns = checked_entry(fields, language)
    except (ValidationFailedError, ContentPolicyBlockedError):
        return None, [*report, ReportItem(index, title, "refused")]
    return ImportedEntry(columns, source), report


def convert_book(raw: Mapping[str, Any], language: str | None, fallback_name: str) -> ImportedBook:
    """Returns the Lyrveil book that a `character_book`, or the `data` of a `lorebook_v3` file, becomes.

    `raw` passed the CharacterBook model. `language` stems every key. `fallback_name` names the book when its own name
    is blank or fails the content guard. A name over 120 characters and a description over 2,000 are cut; a
    `scan_depth` outside 1 to 20 takes 4 and a `token_budget` outside 50 to 900 leaves the book without one, the file's
    values kept in `source_json`. At most 500 entries are imported, in file order. Never raises.
    """
    source: dict[str, Any] = {"fields": {name: value for name, value in raw.items() if name not in _MAPPED_BOOK_FIELDS}}
    raw_name = _text(raw, "name").strip()
    name = _cut(raw_name, MAX_BOOK_NAME_CHARS)
    if not name or find_violation(name) is not None:
        name = _cut(fallback_name.strip(), MAX_BOOK_NAME_CHARS) or DEFAULT_WORLD_NAME
    raw_description = _text(raw, "description").strip()
    description = _cut(raw_description, MAX_DESCRIPTION_CHARS)
    for key, original, stored in (("name", raw_name, name), ("description", raw_description, description)):
        if original != stored:
            source[key] = original
    scan_depth = _integer(raw, "scan_depth", MIN_SCAN_DEPTH, MAX_SCAN_DEPTH)
    token_budget = _integer(raw, "token_budget", MIN_TOKEN_BUDGET, MAX_TOKEN_BUDGET)
    for key, value in (("scan_depth", scan_depth), ("token_budget", token_budget)):
        if value is None and raw.get(key) is not None:
            source[key] = raw[key]
    entries: list[ImportedEntry] = []
    report: list[ReportItem] = []
    raw_entries = raw.get("entries")
    for index, item in enumerate(raw_entries if isinstance(raw_entries, list) else []):
        if len(entries) == MAX_ENTRIES_PER_BOOK:
            report.append(ReportItem(index, _text(item, "name") if isinstance(item, dict) else "", "too_many_entries"))
            break
        if not isinstance(item, dict):
            report.append(ReportItem(index, "", "refused"))
            continue
        entry, found = convert_entry(item, index, language)
        report.extend(found)
        if entry is not None:
            entries.append(entry)
    columns = {
        "name": name,
        "description": description,
        "scan_depth": DEFAULT_SCAN_DEPTH if scan_depth is None else scan_depth,
        "token_budget": token_budget,
    }
    return ImportedBook(columns, source, entries, report)


def character_book_of(source: Mapping[str, Any]) -> dict[str, Any] | None:
    """Returns the raw `character_book` of a stored card `source`, which passed the card parser, or None when the card
    has none (a V1 card never has one)."""
    data = source.get("data") if "spec" in source else None
    book = data.get("character_book") if isinstance(data, dict) else None
    return book if isinstance(book, dict) else None


def _refuse_constant(name: str) -> object:
    raise ValueError(name)


def parse_lorebook(content: bytes) -> dict[str, Any]:
    """Returns the `data` of a `lorebook_v3` file from its bytes: UTF-8 JSON `{"spec": "lorebook_v3", "data": {...}}`.

    Raises PayloadTooLargeError over 64 MB; ImportInvalidError for text that is not UTF-8 JSON, nests more than 64
    levels, has another `spec` or no `data` object, or has a known field of the wrong type (the detail names its path);
    and ContentPolicyBlockedError naming the path of the first string that describes a minor, as a card import does.
    """
    if len(content) > MAX_IMPORT_BYTES:
        raise PayloadTooLargeError("Il file supera il limite di 64 MB.")
    try:
        value = json.loads(content.decode("utf-8-sig"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ImportInvalidError("Il file non è JSON valido.") from error
    if _depth_over(value, MAX_JSON_DEPTH):
        raise ImportInvalidError(f"Il file ha più di {MAX_JSON_DEPTH} livelli annidati.")
    if not isinstance(value, dict) or value.get("spec") != SPEC_LOREBOOK_V3 or not isinstance(value.get("data"), dict):
        raise ImportInvalidError(
            "Il file non è un lorebook V3: serve «spec» uguale a «lorebook_v3» e un oggetto «data»."
        )
    data: dict[str, Any] = value["data"]
    try:
        CharacterBook.model_validate(data)
    except ValidationError as error:
        path = _join_path("data", error.errors()[0]["loc"])
        raise ImportInvalidError(f"Il campo «{path}» del lorebook non è valido.") from error
    check_texts(_card_texts(data, "data"))
    return data


def _merged(*lists: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(key for keys in lists for key in keys))


def _exported_keys(current: Sequence[str], source: Mapping[str, Any], name: str) -> list[str]:
    """Returns the key list `name` of an export: the entry's keys with those the import left out. While the stored list
    is still what the import kept, the file's whole list comes back in its own order."""
    left_out: list[str] = source.get("left_out_keys", {}).get(name, [])
    in_file = source.get("file_keys", {}).get(name)
    if isinstance(in_file, list) and list(current) == [key for key in in_file if key not in left_out]:
        return list(in_file)
    return _merged(current, left_out)


def _restored(current: str, original: object, limit: int) -> str:
    """Returns the file's `original` text when Lyrveil stores it cut to `current` and it was not edited since, else
    `current`."""
    if isinstance(original, str) and _cut(original, limit) == current:
        return original
    return current


def export_entry(entry: LoreEntry) -> dict[str, Any]:
    """Returns the `lorebook_v3` entry of `entry`: its kept fields first, then the mapped ones. The kept decorator
    lines, then an `@@exclude_keys` line for the exclude keys, which the format has no field for, start the content.
    Keys that the import left out return to their lists; an entry the import disabled is enabled again, as it was in
    the file; a name or comment cut on import comes back whole while unedited."""
    source = entry.source
    fields = dict(source.get("fields", {}))
    left_out: dict[str, list[str]] = source.get("left_out_keys", {})
    if entry.secondary_keys or left_out.get("secondary_keys"):
        secondary = _exported_keys(entry.secondary_keys, source, "secondary_keys")
        selective: object = True
    else:
        secondary = list(source.get("secondary_keys", []))
        selective = source.get("selective", False)
    lines = list(source.get("decorators", []))
    exclude = _exported_keys(entry.exclude_keys, source, "exclude_keys")
    if exclude:
        lines.append(f"@@exclude_keys {','.join(exclude)}")
    numbers: dict[str, object] = {}
    for name, default in (("priority", DEFAULT_PRIORITY), ("insertion_order", DEFAULT_INSERTION_ORDER)):
        value = getattr(entry, name)
        numbers[name] = source[name] if name in source and value == default else value
    return {
        **fields,
        "keys": _exported_keys(entry.keys, source, "keys"),
        "content": "\n".join([*lines, entry.content]),
        "extensions": fields.get("extensions", {}),
        "enabled": entry.enabled or "disabled_on_import" in source,
        "case_sensitive": entry.case_sensitive,
        "constant": entry.constant,
        "use_regex": fields.get("use_regex", False),
        "name": _restored(entry.title, source.get("name"), MAX_TITLE_CHARS),
        "comment": _restored(entry.note, source.get("comment"), MAX_NOTE_CHARS),
        "selective": selective,
        "secondary_keys": secondary,
        **numbers,
    }


def _export_order(entry: LoreEntry) -> tuple[int, float, str]:
    """Entries by `insertion_order`; imported ones in file order within it, then the others by id."""
    index = entry.source.get("file_index")
    return entry.insertion_order, index if isinstance(index, int) else math.inf, entry.id


def export_book(book: LoreBook, entries: Sequence[LoreEntry]) -> dict[str, Any]:
    """Returns the book as a `lorebook_v3` `data` object, which is also a card's `character_book`: the kept book fields,
    the name, description, scan depth, token budget (absent when the book has none and the file had none) and the
    entries in export order. A value the import could not use returns while the book still has its default."""
    source = book.source
    fields = dict(source.get("fields", {}))
    data: dict[str, Any] = {
        **fields,
        "name": _restored(book.name, source.get("name"), MAX_BOOK_NAME_CHARS),
        "description": _restored(book.description, source.get("description"), MAX_DESCRIPTION_CHARS),
        "scan_depth": source["scan_depth"]
        if "scan_depth" in source and book.scan_depth == DEFAULT_SCAN_DEPTH
        else book.scan_depth,
        "extensions": fields.get("extensions", {}),
        "entries": [export_entry(entry) for entry in sorted(entries, key=_export_order)],
    }
    if book.token_budget is not None:
        data["token_budget"] = book.token_budget
    elif "token_budget" in source:
        data["token_budget"] = source["token_budget"]
    return data


def lorebook_document(book: LoreBook, entries: Sequence[LoreEntry]) -> dict[str, Any]:
    """Returns the whole `lorebook_v3` file of the book."""
    return {"spec": SPEC_LOREBOOK_V3, "data": export_book(book, entries)}
