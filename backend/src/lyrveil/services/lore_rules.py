"""Validates the editable fields of lore books and entries, stems their keys and applies the content guard (ADR 0049,
ADR 0037). Pure: it reads no database, so LoreService and the unit tests share it."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from lyrveil.consistency.content_guard import find_violation
from lyrveil.domain.errors import ContentPolicyBlockedError, ValidationFailedError
from lyrveil.domain.lore import (
    DEFAULT_INSERTION_ORDER,
    DEFAULT_PRIORITY,
    DEFAULT_SCAN_DEPTH,
    MAX_BOOK_NAME_CHARS,
    MAX_CONTENT_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_KEY_CHARS,
    MAX_KEYS_PER_LIST,
    MAX_NOTE_CHARS,
    MAX_ORDER_MAGNITUDE,
    MAX_SCAN_DEPTH,
    MAX_TITLE_CHARS,
    MAX_TOKEN_BUDGET,
    MIN_SCAN_DEPTH,
    MIN_TOKEN_BUDGET,
    KeyStems,
)
from lyrveil.memory.text import language_code, stems

_KEY_LISTS = (
    ("keys", "parole chiave"),
    ("secondary_keys", "parole chiave secondarie"),
    ("exclude_keys", "parole chiave da escludere"),
)


def guard_text(value: str, label: str) -> None:
    """Raises ContentPolicyBlockedError, naming `label` and never quoting `value`, when the text describes a minor or
    other content the safeguard of ADR 0018 refuses. Lore text is read by the model in every reply that activates it, as
    a character card is, so titles, contents and world names pass the same rules."""
    if find_violation(value) is not None:
        raise ContentPolicyBlockedError(
            f"Il campo «{label}» descrive un minore o un contenuto che l'app non consente. Modifica il testo."
        )


def _text(fields: Mapping[str, Any], name: str, label: str, limit: int) -> str:
    """Returns the trimmed text of `name`, empty when missing. Raises ValidationFailedError when it is not text or is
    over `limit` characters."""
    value = fields.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationFailedError(f"Il campo «{label}» deve essere un testo.")
    value = value.strip()
    if len(value) > limit:
        raise ValidationFailedError(f"Il campo «{label}» supera {limit} caratteri.")
    return value


def _integer(fields: Mapping[str, Any], name: str, label: str, default: int, low: int, high: int) -> int:
    value = fields.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValidationFailedError(f"Il campo «{label}» deve essere un numero intero tra {low} e {high}.")
    return value


def _flag(fields: Mapping[str, Any], name: str, label: str, default: bool) -> bool:
    value = fields.get(name, default)
    if not isinstance(value, bool):
        raise ValidationFailedError(f"Il campo «{label}» deve essere vero o falso.")
    return value


def checked_book(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Returns the columns of a book from its editable `fields`: `name` (required), `description`, `scan_depth` (1 to
    20, default 4) and `token_budget` (None, or 50 to 900).

    Raises ValidationFailedError for a blank or over-long text or a number out of its range, and
    ContentPolicyBlockedError when the name fails the content guard. The description never reaches a prompt, so the
    guard does not read it.
    """
    name = _text(fields, "name", "nome", MAX_BOOK_NAME_CHARS)
    if not name:
        raise ValidationFailedError("Il nome non può essere vuoto.")
    guard_text(name, "nome")
    budget = fields.get("token_budget")
    if budget is not None:
        budget = _integer(
            fields, "token_budget", "budget di token", MIN_TOKEN_BUDGET, MIN_TOKEN_BUDGET, MAX_TOKEN_BUDGET
        )
    return {
        "name": name,
        "description": _text(fields, "description", "descrizione", MAX_DESCRIPTION_CHARS),
        "scan_depth": _integer(
            fields, "scan_depth", "messaggi da leggere", DEFAULT_SCAN_DEPTH, MIN_SCAN_DEPTH, MAX_SCAN_DEPTH
        ),
        "token_budget": budget,
    }


def checked_keys(raw: object, label: str, language: str | None) -> tuple[list[str], list[tuple[str, ...]]]:
    """Returns the keys of one list, trimmed and without blanks or repeats, with the stem sequence of each.

    Raises ValidationFailedError when `raw` is not a list of texts, holds more than 16 keys or a key over 60
    characters, or holds a key with no stem left after stop-word removal, such as `lui`: it could never match, so it is
    refused instead of saved. A language without a stemmer keeps the normalized words as stems, as the memory index
    does.
    """
    if raw is None:
        return [], []
    if not isinstance(raw, Sequence) or isinstance(raw, str) or not all(isinstance(item, str) for item in raw):
        raise ValidationFailedError(f"Il campo «{label}» deve essere un elenco di testi.")
    keys = list(dict.fromkeys(item.strip() for item in raw if item.strip()))
    if len(keys) > MAX_KEYS_PER_LIST:
        raise ValidationFailedError(f"Il campo «{label}» può avere al massimo {MAX_KEYS_PER_LIST} voci.")
    sequences: list[tuple[str, ...]] = []
    for key in keys:
        if len(key) > MAX_KEY_CHARS:
            raise ValidationFailedError(f"Ogni voce di «{label}» può avere al massimo {MAX_KEY_CHARS} caratteri.")
        key_stems = tuple(stems(key, language))
        if not key_stems:
            raise ValidationFailedError(
                f"La parola chiave «{key}» non ha parole utili: contiene solo parole troppo comuni. Scegline un'altra."
            )
        sequences.append(key_stems)
    return keys, sequences


def checked_entry(fields: Mapping[str, Any], language: str | None) -> dict[str, Any]:
    """Returns the editable columns of an entry from `fields`, with the keys stemmed for `language`.

    `fields` holds `content` (required), and optionally `title`, `keys`, `secondary_keys`, `exclude_keys`,
    `case_sensitive`, `constant`, `enabled` (default true), `priority` (default 0), `insertion_order` (default 100) and
    `note`. Text is trimmed; the three key lists are stored as typed, beside `keys_stem_json`, which holds their stems.
    Raises ValidationFailedError for a blank content, a text over its limit (title 80, content 8,000, note 500), a key
    list that breaks `checked_keys`, or a flag or number of the wrong type, and ContentPolicyBlockedError when the title
    or the content fails the content guard. An entry over its block's maximum is valid: it is stored and flagged, not
    refused.
    """
    title = _text(fields, "title", "titolo", MAX_TITLE_CHARS)
    content = _text(fields, "content", "contenuto", MAX_CONTENT_CHARS)
    if not content:
        raise ValidationFailedError("Il contenuto non può essere vuoto.")
    guard_text(title, "titolo")
    guard_text(content, "contenuto")
    columns: dict[str, Any] = {"title": title, "content": content}
    sequences: dict[str, tuple[tuple[str, ...], ...]] = {}
    for name, label in _KEY_LISTS:
        keys, key_stems = checked_keys(fields.get(name), label, language)
        columns[f"{name}_json"] = json.dumps(keys, ensure_ascii=False)
        sequences[name] = tuple(key_stems)
    columns["keys_stem_json"] = KeyStems(
        language=language_code(language) or "",
        keys=sequences["keys"],
        secondary_keys=sequences["secondary_keys"],
        exclude_keys=sequences["exclude_keys"],
    ).to_json()
    columns["case_sensitive"] = _flag(fields, "case_sensitive", "maiuscole e minuscole", False)
    columns["constant"] = _flag(fields, "constant", "sempre attiva", False)
    columns["enabled"] = _flag(fields, "enabled", "attiva", True)
    columns["priority"] = _integer(
        fields, "priority", "priorità", DEFAULT_PRIORITY, -MAX_ORDER_MAGNITUDE, MAX_ORDER_MAGNITUDE
    )
    columns["insertion_order"] = _integer(
        fields, "insertion_order", "ordine", DEFAULT_INSERTION_ORDER, -MAX_ORDER_MAGNITUDE, MAX_ORDER_MAGNITUDE
    )
    columns["note"] = _text(fields, "note", "nota", MAX_NOTE_CHARS)
    return columns
