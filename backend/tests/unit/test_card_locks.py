"""T-CHR-09 and the card diff: locks hold against drafts and proposals, and a diff names the fields that differ."""

from lyrveil.domain.card import CharacterCard
from lyrveil.domain.card_diff import diff_cards
from lyrveil.domain.card_locks import keep_locked


def card(**sections: object) -> CharacterCard:
    return CharacterCard.model_validate({"identity": {"first_name": "Nerea", "age": 27}, **sections})


CURRENT = card(
    traits=[
        {"category": "main_trait", "description": "Ironica.", "locked": True},
        {"category": "fear", "description": "Perdersi.", "locked": False},
    ],
    visual={
        "hair": "Capelli rossi corti.",
        "eyes": "Occhi verdi.",
        "clothing": "Giacca di tela.",
        "identity_en": {"hair_color": "red hair", "hair_length": "short hair", "eye_color": "green eyes"},
        "usual_outfit_en": ["canvas jacket"],
        "locked_fields": ["hair", "clothing"],
    },
)


def test_a_locked_trait_survives_a_draft_or_proposal_that_drops_or_rewrites_it() -> None:
    dropped = card(traits=[{"category": "humor", "description": "Asciutto."}])
    rewritten = card(traits=[{"category": "main_trait", "description": "Timida.", "locked": True}])
    for proposed in (dropped, rewritten):
        kept = keep_locked(CURRENT, proposed).traits
        assert kept[0].model_dump() == {"category": "main_trait", "description": "Ironica.", "locked": True}
    assert [trait.description for trait in keep_locked(CURRENT, dropped).traits] == ["Ironica.", "Asciutto."]


def test_unlocked_traits_follow_the_proposal_and_it_cannot_create_a_lock_or_repeat_a_locked_trait() -> None:
    proposed = card(
        traits=[
            {"category": "main_trait", "description": "Ironica.", "locked": True},
            {"category": "humor", "description": "Asciutto.", "locked": True},
        ]
    )
    traits = keep_locked(CURRENT, proposed).traits
    assert [(trait.description, trait.locked) for trait in traits] == [("Ironica.", True), ("Asciutto.", False)]


def test_a_locked_visual_field_keeps_its_text_slots_and_outfit_while_the_rest_changes() -> None:
    proposed = card(
        visual={
            "hair": "Capelli neri lunghi.",
            "eyes": "Occhi grigi.",
            "clothing": "Abito blu.",
            "identity_en": {"hair_color": "black hair", "hair_length": "long hair", "eye_color": "grey eyes"},
            "usual_outfit_en": ["blue dress"],
            "locked_fields": [],
        }
    )
    visual = keep_locked(CURRENT, proposed).visual
    assert (visual.hair, visual.clothing) == ("Capelli rossi corti.", "Giacca di tela.")
    assert visual.usual_outfit_en == ["canvas jacket"]
    assert (visual.identity_en.hair_color, visual.identity_en.hair_length) == ("red hair", "short hair")
    assert (visual.eyes, visual.identity_en.eye_color) == ("Occhi grigi.", "grey eyes")
    assert visual.locked_fields == ["hair", "clothing"]


def test_a_card_without_locks_is_replaced_by_the_proposal() -> None:
    unlocked = card(traits=[{"category": "fear", "description": "Il buio."}], visual={"hair": "Rossi"})
    proposed = card(traits=[{"category": "humor", "description": "Asciutto."}], visual={"hair": "Neri"})
    assert keep_locked(unlocked, proposed) == proposed


def test_a_diff_lists_the_fields_that_differ_in_card_order() -> None:
    changed = card(
        traits=[{"category": "fear", "description": "Il buio."}],
        visual={"identity_en": {"hair_color": "red hair"}, "seed_policy": "random"},
    )
    changes = diff_cards(card(), changed)
    assert [(change.field, change.before, change.after) for change in changes] == [
        ("traits", [], [{"category": "fear", "description": "Il buio.", "locked": False}]),
        ("visual.identity_en.hair_color", None, "red hair"),
    ]
    assert diff_cards(changed, changed) == []
