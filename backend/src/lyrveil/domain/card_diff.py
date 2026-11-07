"""Field-level comparison of two character cards (docs/consistency-engine.md, Versioning)."""

from dataclasses import dataclass
from typing import Any

from lyrveil.domain.card import CharacterCard


@dataclass(frozen=True)
class FieldChange:
    """One field whose value differs. `field` is a dotted path such as `visual.identity_en.hair_color`; `before` and
    `after` are JSON values. A list (traits, interests, examples, outfits...) is one field and compares whole."""

    field: str
    before: Any
    after: Any


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    flat: dict[str, Any] = {}
    for key, inner in value.items():
        flat.update(_flatten(inner, f"{prefix}.{key}" if prefix else key))
    return flat


def diff_cards(before: CharacterCard, after: CharacterCard) -> list[FieldChange]:
    """Returns the fields that differ between `before` and `after`, in card order: identity, traits, communication and
    visual. Equal cards give an empty list."""
    old, new = _flatten(before.model_dump(mode="json")), _flatten(after.model_dump(mode="json"))
    paths = list(dict.fromkeys([*old, *new]))
    return [FieldChange(path, old.get(path), new.get(path)) for path in paths if old.get(path) != new.get(path)]
