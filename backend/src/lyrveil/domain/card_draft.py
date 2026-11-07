"""CharacterDraft: what the text model writes for assisted creation, and how a draft becomes a card to review.

docs/character-system.md (Assisted creation): the draft has the fields of CharacterCard without locks, seeds and
English tags, which only the user and the tag translation set. `identity` and `communication` are the card's own
models, since they hold none of those. The draft's grammar is generated from this model (lyrveil.prompts.gbnf), and
tests/unit/test_character_draft.py fails when the model stops mirroring the card.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from lyrveil.domain.card import (
    MAX_OUTFITS,
    CharacterCard,
    CommunicationProfile,
    Identity,
    SubjectTag,
    Text,
    TraitCategory,
)
from lyrveil.domain.card_diff import diff_cards

# The card fields a draft never writes, as dotted paths in the card; a list item's field is named under the list.
DRAFT_EXCLUDED_FIELDS = frozenset(
    {
        "traits.locked",
        "visual.identity_en",
        "visual.usual_outfit_en",
        "visual.outfits.tags_en",
        "visual.persistent_negative_en",
        "visual.locked_fields",
        "visual.seed_policy",
        "visual.base_seed",
    }
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftTrait(_Strict):
    category: TraitCategory
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class DraftOutfit(_Strict):
    """An outfit without English tags. The key rules of the card's Outfit apply when the draft becomes a card."""

    key: str
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class DraftVisual(_Strict):
    face: Text = ""
    hair: Text = ""
    eyes: Text = ""
    body: Text = ""
    clothing: Text = ""
    accessories: Text = ""
    distinctive_marks: Text = ""
    art_style: Text = ""
    subject_tag: SubjectTag = "1other"
    outfits: Annotated[list[DraftOutfit], Field(max_length=MAX_OUTFITS)] = []


class CharacterDraft(_Strict):
    identity: Identity
    traits: list[DraftTrait] = []
    communication: CommunicationProfile = Field(default_factory=CommunicationProfile)
    visual: DraftVisual = Field(default_factory=DraftVisual)


def card_as_draft(card: CharacterCard) -> CharacterDraft:
    """Returns the fields of `card` that a draft has: what the model sees of an existing character. A stored card always
    converts, because the draft's bounds are the card's."""
    raw = card.model_dump(mode="json")
    visual = {name: raw["visual"][name] for name in DraftVisual.model_fields if name != "outfits"}
    visual["outfits"] = [{key: outfit[key] for key in DraftOutfit.model_fields} for outfit in raw["visual"]["outfits"]]
    traits = [{key: trait[key] for key in DraftTrait.model_fields} for trait in raw["traits"]]
    return CharacterDraft.model_validate({**raw, "traits": traits, "visual": visual})


def draft_to_raw_card(draft: CharacterDraft, current: CharacterCard | None) -> dict[str, Any]:
    """Returns the raw card a draft proposes, to validate with lyrveil.services.card_rules.validate_card.

    Without `current` (a new character) the fields a draft lacks take the card's defaults. Over an existing card they
    keep `current`'s values: the locks, the seed policy, the English slots and the negative prompt, and the English tags
    of an outfit whose key the draft keeps. The locks themselves are applied afterwards with `keep_locked`, which only
    the stored card can drive. Neither argument changes.
    """
    proposed = draft.model_dump(mode="json")
    if current is None:
        return proposed
    visual = current.visual.model_dump(mode="json")
    tags = {outfit.key: list(outfit.tags_en) for outfit in current.visual.outfits}
    outfits = [{**outfit, "tags_en": tags.get(outfit["key"], [])} for outfit in proposed["visual"].pop("outfits")]
    return {**proposed, "visual": {**visual, **proposed["visual"], "outfits": outfits}}


def proposed_fields(card: CharacterCard, current: CharacterCard | None) -> list[str]:
    """Returns the dotted paths, in card order, of the fields the draft filled in: for a new character every field
    that differs from the card's default, plus the first name and the age, which have none; over an existing card the
    fields it changed. These are the fields the Creator marks as proposed."""
    if current is not None:
        return [change.field for change in diff_cards(current, card)]
    blank = CharacterCard(identity=Identity(first_name=card.identity.first_name, age=card.identity.age))
    changed = [change.field for change in diff_cards(blank, card)]
    return ["identity.first_name", "identity.age", *changed]
