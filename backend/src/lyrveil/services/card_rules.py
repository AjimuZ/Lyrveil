"""Validates a character card: the domain model's shape, then the tag rules that need the vocabulary and sanitizer."""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from lyrveil.domain.card import CharacterCard, VisualIdentityEn, VisualProfile
from lyrveil.domain.errors import ValidationFailedError
from lyrveil.images.sanitizer import sanitize_tag
from lyrveil.images.tags import TagVocabulary, load_vocabulary

MAX_FREE_TAG_WORDS = 4
_SINGLE_SLOTS = ("hair_color", "hair_length", "eye_color", "skin")
_LIST_SLOTS = ("hairstyle", "face", "body", "marks", "accessories", "style")
_FIELD_MESSAGES = {
    ("identity", "first_name"): "Il nome deve avere da 1 a 80 caratteri.",
    ("identity", "last_name"): "Il cognome può avere al massimo 80 caratteri.",
    ("identity", "grammatical_gender"): "Il genere grammaticale non è valido.",
    ("identity", "primary_language"): "La lingua non è supportata.",
}


def _path(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location)


def describe_error(error: ValidationError) -> str:
    """Returns the Italian detail for the first problem pydantic found in a card or a draft. It names the field, never
    the value."""
    first = error.errors()[0]
    location, kind = first["loc"], first["type"]
    if not location:
        return "La scheda del personaggio non è valida."
    if location[:2] == ("identity", "age"):
        if kind == "greater_than_equal":
            return "L'età deve essere almeno 18."
        if kind == "less_than_equal":
            return "L'età deve essere al massimo 120."
        return "L'età deve essere un numero intero."
    if kind == "value_error":
        return str(first["ctx"]["error"])
    if kind == "extra_forbidden":
        return f"Il campo «{_path(location)}» non è previsto."
    if kind == "missing":
        return f"Manca il campo «{_path(location)}»."
    return _FIELD_MESSAGES.get((str(location[0]), str(location[-1])), f"Il campo «{_path(location)}» non è valido.")


def _free_tags(path: str, tags: list[str]) -> list[str]:
    """Returns the sanitized tags without blanks or repeats, in order. Raises for a tag of more than 4 words."""
    cleaned: list[str] = []
    for raw in tags:
        tag = sanitize_tag(raw)
        if not tag:
            continue
        if len(tag.split()) > MAX_FREE_TAG_WORDS:
            raise ValidationFailedError(f"Ogni tag di «{path}» può avere al massimo 4 parole.")
        if tag not in cleaned:
            cleaned.append(tag)
    return cleaned


def _slot_tag(vocabulary: TagVocabulary, category: str, path: str, raw: str) -> str:
    tag = sanitize_tag(raw)
    if not vocabulary.contains(category, tag):
        raise ValidationFailedError(f"Il valore di «{path}» non è nel vocabolario dei tag.")
    return tag


def _identity(identity: VisualIdentityEn, vocabulary: TagVocabulary) -> VisualIdentityEn:
    base = "visual.identity_en"
    changes: dict[str, Any] = {}
    for slot in _SINGLE_SLOTS:
        value: str | None = getattr(identity, slot)
        if value is not None:
            changes[slot] = _slot_tag(vocabulary, slot, f"{base}.{slot}", value)
    for slot in _LIST_SLOTS:
        tags = [_slot_tag(vocabulary, slot, f"{base}.{slot}", raw) for raw in getattr(identity, slot)]
        changes[slot] = list(dict.fromkeys(tags))
    changes["extra"] = _free_tags(f"{base}.extra", identity.extra)
    return identity.model_copy(update=changes)


def _visual(visual: VisualProfile, vocabulary: TagVocabulary) -> VisualProfile:
    outfits = [
        outfit.model_copy(update={"tags_en": _free_tags(f"visual.outfits.{index}.tags_en", outfit.tags_en)})
        for index, outfit in enumerate(visual.outfits)
    ]
    return visual.model_copy(
        update={
            "identity_en": _identity(visual.identity_en, vocabulary),
            "usual_outfit_en": _free_tags("visual.usual_outfit_en", visual.usual_outfit_en),
            "outfits": outfits,
        }
    )


def validate_card(raw: Mapping[str, Any]) -> CharacterCard:
    """Returns the card of `raw`, a mapping with `identity` and optionally `traits`, `communication` and `visual`.

    Text is trimmed, slot tags are sanitized and checked against the vocabulary, and free tags are sanitized, limited to
    4 words and de-duplicated. Raises ValidationFailedError with an Italian detail for an unknown field, a value out of
    its bounds (an age under 18 among them), a slot value the vocabulary lacks or a reserved outfit key.
    """
    try:
        card = CharacterCard.model_validate(raw)
    except ValidationError as error:
        raise ValidationFailedError(describe_error(error)) from error
    return card.model_copy(update={"visual": _visual(card.visual, load_vocabulary())})
