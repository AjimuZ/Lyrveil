"""T-SEC-07, the cases of the content guard (docs/security.md, Content safeguards): every rule in Italian and English,
the childhood phrases that pass, every text field of a card, the draft description, and that no setting or
configuration file can change the minor safeguards (ADR 0018). The entry points are in
tests/integration/test_content_guard.py and tests/integration/test_character_drafts.py."""

import ast
import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lyrveil.consistency import content_guard
from lyrveil.consistency.content_guard import (
    UNCHECKED_FIELDS,
    GuardRule,
    check_card,
    check_draft_description,
    find_violation,
    normalize,
)
from lyrveil.domain import card as card_module
from lyrveil.domain.card import MIN_AGE, CharacterCard
from lyrveil.domain.errors import ContentPolicyBlockedError, ValidationFailedError
from lyrveil.prompts.character_draft import EXAMPLE_DESCRIPTION
from lyrveil.services.card_rules import validate_card
from lyrveil.settings import Settings, load_settings

TERM = GuardRule.MINOR_TERM
AGE = GuardRule.MINOR_AGE
SEXUAL = GuardRule.CHILDHOOD_SEXUAL


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        # Minor terms, Italian
        ("È una bambina curiosa.", TERM),
        ("Una ragazzina timida che vive al porto.", TERM),
        ("Minorenne in fuga da casa.", TERM),
        ("Adolescente ribelle.", TERM),
        ("Liceale al primo anno.", TERM),
        ("Frequenta le medie a Velmara.", TERM),
        ("Ama i libri per bambini.", TERM),
        ("Ha l'aspetto di una loli.", TERM),
        ("Disegnata in stile shota.", TERM),
        # A childhood phrase passes, the same word elsewhere in the sentence does not.
        ("Da bambina era una bambina felice.", TERM),
        # A child's costume or act, even with the words of a childhood phrase
        ("Si veste da bambina.", TERM),
        ("A volte si comporta da ragazzina.", TERM),
        # Minor terms, English
        ("She is a child.", TERM),
        ("A shy teenager from Leeds.", TERM),
        ("Underage runaway.", TERM),
        ("A schoolgirl from the north.", TERM),
        ("She has two kids.", TERM),
        ("She dresses up as a little girl.", TERM),
        # Case, accents, invisible characters, spaced letters and digit swaps
        ("BAMBÌNA", TERM),
        ("bam​bina", TERM),
        ("b a m b i n a", TERM),
        ("b4mbina", TERM),
        ("l.o.l.i", TERM),
        # Ages under 18, Italian
        ("Ha 16 anni.", AGE),
        ("Ha solo quindici anni.", AGE),
        ("Una sedicenne di provincia.", AGE),
        ("Una 16enne curiosa.", AGE),
        ("Una ragazza di 15 anni.", AGE),
        ("Ha una figlia di 8 anni.", AGE),
        ("Giulia, 16 anni, studentessa.", AGE),
        ("Età: 16", AGE),
        ("Compie 17 anni domani.", AGE),
        ("Ha quasi 18 anni.", AGE),
        ("16 anni compiuti.", AGE),
        ("Sembra avere 14 anni.", AGE),
        # Ages under 18, English
        ("She is 16 years old.", AGE),
        ("A 15-year-old girl.", AGE),
        ("Aged 12.", AGE),
        ("16yo", AGE),
        ("16 y.o.", AGE),
        ("She's sixteen.", AGE),
        ("I'm 15 and bored.", AGE),
        ("Under 18.", AGE),
        # A childhood reference next to a sexual term
        ("Da bambina ha avuto esperienze sessuali.", SEXUAL),
        ("A 15 anni posava nuda.", SEXUAL),
        ("Durante l'infanzia ha subito abusi sessuali.", SEXUAL),
        ("As a child she watched porn.", SEXUAL),
        ("When she was 14 she had sex.", SEXUAL),
    ],
)
def test_text_that_describes_a_minor_is_refused(text: str, rule: GuardRule) -> None:
    assert find_violation(text) is rule


@pytest.mark.parametrize(
    "text",
    [
        # The character's own past
        "Da bambina sognava il mare.",
        "Fin da piccola disegna mappe.",
        "Quando era ragazzina viveva a Genova.",
        "Quand'era bambino giocava a scopa con il nonno.",
        "A 16 anni ha lasciato casa.",
        "Aveva 12 anni quando si è trasferita.",
        "All'età di 10 anni ha imparato a nuotare.",
        "Da bambina correva a piedi nudi sulla spiaggia.",
        "Da bambina viveva al mare. Oggi scrive romanzi erotici.",
        "As a child she loved maps.",
        "When she was a teenager she moved to Rome.",
        "At 16 she left home.",
        "She is an only child.",
        # Adult ages, durations and age differences
        "Ha 27 anni.",
        "Ha 3 anni di esperienza come cartografa.",
        "Ha due anni per finire la mappa.",
        "Ha un anno sabbatico.",
        "Lavora da 10 anni al porto.",
        "Dieci anni fa ha lasciato la gilda.",
        "Dopo una relazione di 5 anni vive da sola.",
        "Il suo caffè compie dieci anni a maggio.",
        "A quell'età sei ancora un sognatore.",
        "Tra loro c'è una differenza di età di 10 anni.",
        "Un barista di 34 anni, calmo e un po' malinconico.",
        "She is 27 years old.",
        "She is twenty-one years old.",
        "She is 5 feet tall.",
        "She is one of the best mapmakers.",
        "Meet me at 10 pm, sexy.",
        # Words close to a list entry
        "Sorella minore di Marco.",
        "She plays in a minor key.",
        "Ottenne il diploma con lode.",
        "Per decenni ha viaggiato per mare.",
        "Insegna al liceo.",
        "Sensuale e sicura di sé.",
        # The example card of docs/character-system.md (Exchange format)
        "È cresciuta sopra una bottega di bussole e ha imparato a disegnare prima che a leggere.",
        "Ha lasciato la gilda dei cartografi dopo una discussione sui confini.",
        "ok, ammetto che era al contrario. ma solo un po'",
    ],
)
def test_adult_text_and_the_childhood_phrases_pass(text: str) -> None:
    assert find_violation(text) is None


FULL_CARD: dict[str, Any] = {
    "identity": {
        "first_name": "Nerea",
        "last_name": "Falchi",
        "age": 27,
        "grammatical_gender": "femminile",
        "narrative_identity": "Cartografa di una città portuale.",
        "biography": "È cresciuta sopra una bottega di bussole.",
        "occupation": "Cartografa indipendente",
        "interests": ["mappe antiche"],
        "background": "Ha lasciato la gilda dei cartografi.",
        "setting": "Velmara, città di mare",
        "primary_language": "it",
    },
    "traits": [{"category": "main_trait", "description": "Ironica, curiosa.", "locked": False}],
    "communication": {
        "punctuation": "Pochi punti esclamativi.",
        "colloquial_style": "Scrive di getto.",
        "examples": [{"user": "La mappa è pronta?", "character": "quasi, manca il porto"}],
        "recurring_expressions": ["ma solo un po'"],
        "avoid": ["Che bella domanda!"],
    },
    "visual": {
        "face": "Viso spigoloso.",
        "hair": "Capelli rossi corti.",
        "eyes": "Occhi verde scuro.",
        "body": "Snella.",
        "clothing": "Giacca di tela.",
        "accessories": "Bussola al collo.",
        "distinctive_marks": "Inchiostro sulle dita.",
        "art_style": "Illustrazione pulita.",
        "subject_tag": "1girl",
        "identity_en": {
            "hair_color": "red hair",
            "hair_length": "short hair",
            "eye_color": "green eyes",
            "skin": "pale skin",
            "hairstyle": ["messy hair"],
            "face": ["freckles"],
            "body": ["slim"],
            "marks": ["ink stains"],
            "accessories": ["necklace"],
            "style": ["soft colors"],
            "extra": ["brass compass pendant"],
        },
        "usual_outfit_en": ["canvas jacket"],
        "outfits": [{"key": "festa", "label": "Festa", "description": "Abito blu.", "tags_en": ["blue dress"]}],
        "persistent_negative_en": "blurry",
        "locked_fields": ["hair"],
    },
}


def string_paths(value: object, path: str = "") -> list[str]:
    """Returns the dotted path of every string below `value`."""
    if isinstance(value, str):
        return [path]
    children: list[tuple[str, object]] = []
    if isinstance(value, dict):
        children = [(f"{path}.{key}" if path else str(key), item) for key, item in value.items()]
    elif isinstance(value, list):
        children = [(f"{path}.{index}", item) for index, item in enumerate(value)]
    return [found for child, item in children for found in string_paths(item, child)]


def replaced(raw: dict[str, Any], path: str, value: str) -> dict[str, Any]:
    """Returns a copy of `raw` with the string at the dotted `path` replaced by `value`."""
    changed = copy.deepcopy(raw)
    *parents, last = path.split(".")
    target: Any = changed
    for part in parents:
        target = target[int(part)] if isinstance(target, list) else target[part]
    if isinstance(target, list):
        target[int(last)] = value
    else:
        target[last] = value
    return changed


def test_every_text_of_a_card_is_checked_and_the_detail_names_the_field_only() -> None:
    dumped = CharacterCard.model_validate(FULL_CARD).model_dump(mode="json")
    check_card(CharacterCard.model_validate(dumped))
    checked: list[str] = []
    for path in string_paths(dumped):
        try:
            card = CharacterCard.model_validate(replaced(dumped, path, "bambina"))
        except ValidationError:
            continue  # A closed list cannot hold free text.
        if path in UNCHECKED_FIELDS:
            check_card(card)
            continue
        with pytest.raises(ContentPolicyBlockedError) as refused:
            check_card(card)
        assert refused.value.detail == f"Il campo «{path}» parla di un minore. La scheda descrive solo adulti."
        checked.append(path)
    assert {
        "identity.first_name",
        "identity.last_name",
        "identity.biography",
        "identity.interests.0",
        "traits.0.description",
        "communication.examples.0.user",
        "communication.examples.0.character",
        "communication.avoid.0",
        "visual.body",
        "visual.identity_en.hair_color",
        "visual.identity_en.extra.0",
        "visual.usual_outfit_en.0",
        "visual.outfits.0.key",
        "visual.outfits.0.label",
        "visual.outfits.0.description",
        "visual.outfits.0.tags_en.0",
    } <= set(checked)


def test_the_first_offending_field_in_card_order_is_named() -> None:
    raw = copy.deepcopy(FULL_CARD)
    raw["identity"]["background"] = "Ha 15 anni."
    raw["traits"][0]["description"] = "Una ragazzina timida."
    with pytest.raises(ContentPolicyBlockedError) as refused:
        check_card(CharacterCard.model_validate(raw))
    assert refused.value.detail == (
        "Il campo «identity.background» indica un'età sotto i 18 anni. La scheda descrive solo adulti."
    )


def test_the_negative_prompt_holds_the_safety_terms_and_is_not_checked() -> None:
    raw = copy.deepcopy(FULL_CARD)
    raw["visual"]["persistent_negative_en"] = "child, loli, shota, toddler, underage, minor"
    check_card(CharacterCard.model_validate(raw))


def test_validate_card_applies_the_guard_after_the_schema() -> None:
    with pytest.raises(ContentPolicyBlockedError) as refused:
        validate_card({"identity": {"first_name": "Alba", "age": 30, "biography": "Una ragazzina timida."}})
    assert refused.value.code == "CONTENT_POLICY_BLOCKED"
    assert refused.value.status == 422
    # The age rule stays a validation error of the schema, and a guard refusal is never mistaken for one.
    with pytest.raises(ValidationFailedError, match="L'età deve essere almeno 18"):
        validate_card({"identity": {"first_name": "Alba", "age": 17}})
    assert not issubclass(ContentPolicyBlockedError, ValidationFailedError)


DESCRIPTION_AGE = "La descrizione indica un'età sotto i 18 anni. Lyrveil crea solo personaggi adulti."
DESCRIPTION_CHILDHOOD = "La descrizione accosta l'infanzia a un contenuto sessuale."


def test_a_draft_description_is_checked_before_the_model_runs() -> None:
    with pytest.raises(ContentPolicyBlockedError) as refused:
        check_draft_description("Crea una ragazza di 16 anni, timida.")
    assert refused.value.detail == DESCRIPTION_AGE
    with pytest.raises(ContentPolicyBlockedError) as childhood:
        check_draft_description("Crea un personaggio che da bambina posava nuda.")
    assert childhood.value.detail == DESCRIPTION_CHILDHOOD
    check_draft_description(EXAMPLE_DESCRIPTION)
    check_draft_description("Crea un personaggio di 26 anni, appassionato di fantasy, ironico e indipendente.")


LISTS = {
    "MINOR_TERMS": content_guard.MINOR_TERMS,
    "NOT_MINOR_PHRASES": content_guard.NOT_MINOR_PHRASES,
    "CHILD_WORDS_IT": content_guard.CHILD_WORDS_IT,
    "CHILD_WORDS_EN": content_guard.CHILD_WORDS_EN,
    "CHILDHOOD_PERIODS": content_guard.CHILDHOOD_PERIODS,
    "SEXUAL_TERMS": content_guard.SEXUAL_TERMS,
    "SEXUAL_IDIOMS": content_guard.SEXUAL_IDIOMS,
    "AGE_WORDS_IT": content_guard.AGE_WORDS_IT,
    "PERSON_WORDS_IT": content_guard.PERSON_WORDS_IT,
    "PERSON_WORDS_EN": content_guard.PERSON_WORDS_EN,
    "NUMBERS_IT": content_guard.NUMBERS_IT,
    "NUMBERS_EN": content_guard.NUMBERS_EN,
    "TENS_EN": content_guard.TENS_EN,
}


@pytest.mark.parametrize("name", sorted(LISTS))
def test_every_list_entry_is_written_as_normalized_text(name: str) -> None:
    """An entry with an accent, a capital or a hyphen could never match, since text is compared normalized."""
    entries = LISTS[name]
    assert entries
    for entry in entries:
        word = entry.removesuffix("*")
        assert word, name
        assert normalize(word) == word, f"{name}: {entry!r}"


def test_each_minor_term_is_refused_on_its_own() -> None:
    for entry in content_guard.MINOR_TERMS:
        text = entry.removesuffix("*") + ("o" if entry.endswith("*") else "")
        assert find_violation(text) is GuardRule.MINOR_TERM, entry


def test_each_italian_age_word_is_refused_on_its_own() -> None:
    for entry in content_guard.AGE_WORDS_IT:
        assert find_violation(f"Una {entry}.") is GuardRule.MINOR_AGE, entry


# What the guard may import, and names that would let a setting, the environment or a file reach it.
ALLOWED_IMPORTS = {"re", "unicodedata", "collections.abc", "enum", "lyrveil.domain.card", "lyrveil.domain.errors"}
FORBIDDEN_NAMES = {
    "open",
    "os",
    "sys",
    "environ",
    "getenv",
    "Path",
    "pathlib",
    "read_text",
    "read_bytes",
    "settings",
    "Settings",
    "load_settings",
    "importlib",
    "import_module",
    "__import__",
    "eval",
    "exec",
    "setattr",
    "globals",
}
HOSTILE_ENVIRONMENT = {
    "LYRVEIL_CONTENT_GUARD": "off",
    "LYRVEIL_CONTENT_GUARD_ENABLED": "false",
    "LYRVEIL_MIN_AGE": "0",
    "LYRVEIL_ALLOW_MINORS": "true",
    "LYRVEIL_EXPLICIT_MODE": "true",
}


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


def test_no_setting_or_configuration_file_can_change_the_minor_safeguards(monkeypatch: pytest.MonkeyPatch) -> None:
    # The guard reads nothing from outside its own module: no settings, no environment, no file.
    guard_path = Path(content_guard.__file__)
    assert imports_of(guard_path) <= ALLOWED_IMPORTS
    tree = ast.parse(guard_path.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not (names | attributes) & FORBIDDEN_NAMES
    # The age rule is a constant of the card schema, which imports no setting either.
    assert imports_of(Path(card_module.__file__)) <= {"re", "typing", "pydantic"}
    assert MIN_AGE == 18
    assert CharacterCard.model_json_schema()["$defs"]["Identity"]["properties"]["age"]["minimum"] == 18
    # No deployment setting names a safeguard, and variables that look like one are ignored.
    words = {word for field in Settings.model_fields for word in field.split("_")}
    assert not words & {"guard", "safeguard", "safety", "minor", "minors", "adult", "age", "content", "explicit"}
    monkeypatch.setenv("LYRVEIL_CONTROL_TOKEN", "ab" * 32)
    for name, value in HOSTILE_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    settings = load_settings()
    hostile_fields = {name.removeprefix("LYRVEIL_").lower() for name in HOSTILE_ENVIRONMENT}
    assert not hostile_fields & set(Settings.model_fields)
    assert not any(hasattr(settings, field) for field in hostile_fields)
    assert find_violation("Una ragazzina timida.") is GuardRule.MINOR_TERM
    with pytest.raises(ValidationFailedError):
        validate_card({"identity": {"first_name": "Alba", "age": 17}})
    with pytest.raises(ContentPolicyBlockedError):
        validate_card({"identity": {"first_name": "Alba", "age": 30, "occupation": "Studentessa delle medie"}})
