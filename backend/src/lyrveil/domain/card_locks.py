"""Locks of the character card: what a draft or a proposal may not change (docs/consistency-engine.md, Versioning)."""

from lyrveil.domain.card import CharacterCard, Trait

# The identity slots each lockable visual field protects (ADR 0035). Every lockable field also protects the Italian
# description of the same name, and `clothing` protects the usual outfit instead of a slot.
PROTECTED_SLOTS: dict[str, tuple[str, ...]] = {
    "hair": ("hair_color", "hair_length", "hairstyle"),
    "eyes": ("eye_color",),
    "face": ("face", "skin"),
    "body": ("body",),
    "distinctive_marks": ("marks",),
    "accessories": ("accessories",),
    "art_style": ("style",),
    "clothing": (),
}


def keep_locked(current: CharacterCard, proposed: CharacterCard) -> CharacterCard:
    """Returns `proposed` with everything `current` locks put back, for a draft or a proposal that is not the user's own
    edit.

    Locked traits of `current` stay as they are and come first, in their saved order. The traits of `proposed` follow,
    unlocked: automation cannot create a lock, and a proposed trait equal to a locked one is dropped as a repeat. In the
    visual profile, each field of `current.visual.locked_fields` keeps its Italian text and the slots or outfit it
    protects, and the list of locks itself stays `current`'s. Nothing else is touched, and neither argument changes.
    """
    locked = [trait for trait in current.traits if trait.locked]
    seen = {(trait.category, trait.description) for trait in locked}
    free = [
        Trait(category=trait.category, description=trait.description)
        for trait in proposed.traits
        if (trait.category, trait.description) not in seen
    ]
    text_fields: dict[str, object] = {"locked_fields": list(current.visual.locked_fields)}
    slots: dict[str, object] = {}
    for field in current.visual.locked_fields:
        text_fields[field] = getattr(current.visual, field)
        for slot in PROTECTED_SLOTS[field]:
            slots[slot] = getattr(current.visual.identity_en, slot)
        if field == "clothing":
            text_fields["usual_outfit_en"] = list(current.visual.usual_outfit_en)
    identity_en = proposed.visual.identity_en.model_copy(update=slots)
    visual = proposed.visual.model_copy(update={**text_fields, "identity_en": identity_en})
    return proposed.model_copy(update={"traits": [*locked, *free], "visual": visual})
