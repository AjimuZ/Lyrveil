"""Tests of lyrveil/domain/card_mapping.py and lyrveil/domain/card_placeholders.py: T-CRD-03 (the direct mapping, the
macros and the report), the age part of T-CRD-04, and T-CRD-06 (the placeholders render at prompt time, and the card
fields that never reach a prompt stay out of the reply, proactive and draft prompts).

The cards are the synthetic fixtures of tests/fixtures/tavern_cards and small cards written here. The prompt tests use a
fake runtime whose tokenizer counts whitespace-separated words, as tests/unit/test_context_builder.py does.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lyrveil.context.builder import Assembly, ContextBuilder, ContextRequest
from lyrveil.domain.card import CharacterCard, Identity
from lyrveil.domain.card_draft import CharacterDraft, card_as_draft
from lyrveil.domain.card_mapping import (
    MAX_EXAMPLES,
    DirectMapping,
    ImportedDraft,
    ImportedIdentity,
    map_card,
    scenarios_to_create,
)
from lyrveil.domain.card_placeholders import MAX_MACRO_DEPTH, MacroAction, expand_macros, render_text
from lyrveil.domain.messages import Author, Message, MessageStatus
from lyrveil.domain.personas import ChatPersona
from lyrveil.domain.tavern_card import CardData, parse_card
from lyrveil.ports.memory_retriever import RetrievalQuery, RetrievalResult
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.prompts import character_draft
from lyrveil.prompts.formatter import formatter_for

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tavern_cards"
NOW = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
ANNA = ChatPersona(name="Anna")
# The texts of the v2 fixture that no prompt may carry.
NEVER_IN_A_PROMPT = (
    "Write as {{char}} in third person.",
    "Write as Livia",
    "Keep replies under three paragraphs.",
    "Synthetic card written for the Lyrveil parser tests.",
)


def _fixture(name: str) -> DirectMapping:
    parsed = parse_card((FIXTURES / name).read_bytes())
    return map_card(parsed.data, prefix="" if name == "v1.json" else "data")


def _mapped(**fields: Any) -> DirectMapping:
    return map_card(CardData.model_validate({"name": "Mara", **fields}))


def _report(mapping: DirectMapping) -> dict[str, Any]:
    report = mapping.report_json()
    json.dumps(report)  # The report is stored as JSON text.
    return report


# T-CRD-03: the direct mapping


def test_the_v2_fixture_maps_as_the_table() -> None:
    mapping = _fixture("v2.json")
    draft = mapping.draft

    assert draft.identity.first_name == "Livia Sarno"
    assert draft.identity.biography.startswith("{{char}} is a 31 years old lighthouse keeper")
    assert draft.identity.grammatical_gender == "non_indicato"
    assert draft.identity.primary_language == "it"
    assert [(trait.category, trait.description) for trait in draft.traits] == [
        ("main_trait", "Dry humour, generous with tea, distrustful of calm seas."),
        ("main_trait", "She hums old sea songs when she is nervous."),
    ]
    assert [(pair.user, pair.character) for pair in draft.communication.examples] == [
        ("Does the light ever go out?", "Not while I am awake. And I sleep very little."),
        (None, "Ship on the horizon. Grab the binoculars."),
    ]
    assert draft.visual.model_dump() == ImportedDraft(identity=ImportedIdentity()).visual.model_dump()
    assert mapping.card_fields == [
        "identity.first_name",
        "identity.age",
        "identity.biography",
        "traits",
        "communication.examples",
    ]
    assert [scenario.title for scenario in mapping.scenarios] == [
        "Livia Sarno",
        "Livia Sarno (saluto 2)",
        "Livia Sarno (saluto 3)",
    ]
    assert [scenario.opening_message.split(".*")[0] for scenario in mapping.scenarios] == [
        "*Livia holds the door against the wind",
        "*The lamp turns slowly overhead",
        "*Livia waves from the gallery",
    ]
    assert {scenario.description for scenario in mapping.scenarios} == {
        "A storm strands {{user}} at the lighthouse for the night."
    }
    stored = json.dumps(mapping.draft_json())
    for text in NEVER_IN_A_PROMPT:
        assert text not in stored


def test_a_v1_card_names_its_sources_without_the_data_prefix() -> None:
    mapping = _fixture("v1.json")

    assert mapping.age.source == "description"
    assert mapping.draft.identity.age == 52
    assert mapping.draft.communication.examples[0].user == "Can you fix it today?"


def test_a_long_paragraph_becomes_traits_cut_at_sentence_ends() -> None:
    sentences = [f"Frase numero {number} " + "parola " * 30 + "fine." for number in range(1, 7)]
    mapping = _mapped(personality=" ".join(sentences) + "\n\nBreve.")

    descriptions = [trait.description for trait in mapping.draft.traits]
    assert all(len(text) <= 500 for text in descriptions)
    assert len(descriptions) > 2
    assert descriptions[-1] == "Breve."
    assert all(text.endswith("fine.") for text in descriptions[:-1]), "every cut falls at a sentence end"
    assert " ".join(descriptions[:-1]) == " ".join(sentences)


def test_example_blocks_over_the_limits_are_left_out_and_reported() -> None:
    blocks = [
        "{{user}}: Ciao.\n{{char}}: Ciao a te.\n*Sorride.*",
        "{{user}}: " + "u" * 301 + "\n{{char}}: Troppo lungo.",
        "{{user}}: Corto.\n{{char}}: " + "c" * 501,
        "{{user}}: Nessuna risposta.",
        *[f"{{{{char}}}}: Esempio {number}." for number in range(MAX_EXAMPLES)],
    ]
    mapping = _mapped(mes_example="\n".join(f"<START>\n{block}" for block in blocks))

    examples = mapping.draft.communication.examples
    assert len(examples) == MAX_EXAMPLES
    assert (examples[0].user, examples[0].character) == ("Ciao.", "Ciao a te.\n*Sorride.*")
    assert examples[-1].character == f"Esempio {MAX_EXAMPLES - 2}."
    left = [(item["item"], item["reason"]) for item in _report(mapping)["left_out"]]
    assert left == [
        (2, "example_user_too_long"),
        (3, "example_character_too_long"),
        (4, "no_character_line"),
        (12, "too_many_examples"),
    ]
    assert {item["source"] for item in _report(mapping)["left_out"]} == {"data.mes_example"}


def test_a_block_takes_the_first_user_turn_and_every_character_turn() -> None:
    turns = ("{{char}}: Prima.", "{{user}}: Domanda?", "{{char}}: Dopo.", "{{user}}: Altra.", "{{char}}: Fine.")
    mapping = _mapped(mes_example="\n".join(turns))

    (pair,) = mapping.draft.communication.examples
    assert (pair.user, pair.character) == ("Domanda?", "Prima.\nDopo.\nFine.")


@pytest.mark.parametrize(
    ("text", "expected", "actions"),
    [
        ("<BOT> saluta <USER> e <char>.", "{{char}} saluta {{user}} e {{char}}.", ["placeholder"] * 3),
        ("<bot> e <User>", "{{char}} e {{user}}", ["placeholder"] * 2),
        ("{{CHAR}} e {{ user }}", "{{CHAR}} e {{ user }}", []),
        ("a{{// nota}}b{{comment: x}}c{{hidden_key: k}}d", "abcd", ["removed"] * 3),
        ("{{random: rosso, blu}} {{pick::uno::due}} {{random::a::b}}", "rosso uno a", ["first_option"] * 3),
        ("{{reverse: abc}}", "cba", ["reversed"]),
        ("{{roll: 1d6}}{{time}}{{newline}}", "", ["removed"] * 3),
        ("{{random: {{char}}, {{user}}}}", "{{char}}", ["first_option"]),
        ("{{random: <USER>, x}}", "{{user}}", ["placeholder", "first_option"]),
        ("solo {{ aperta e chiusa }} }} {{", "solo  }} {{", ["removed"]),
    ],
)
def test_macros_are_replaced_or_removed(text: str, expected: str, actions: list[str]) -> None:
    expanded, uses = expand_macros(text)

    assert expanded == expected
    assert [use.action.value for use in uses] == actions


def test_deep_or_unpaired_braces_cost_no_recursion() -> None:
    expanded, uses = expand_macros("{{" * 5000 + "x" + "}}" * 5000)
    assert expanded == ""
    assert len(uses) == MAX_MACRO_DEPTH + 1, "the levels below the limit are removed whole, without a report each"
    assert all(use.action is MacroAction.REMOVED for use in uses)

    unpaired = "{{" * 20000 + "{{char}}"
    assert expand_macros(unpaired)[0] == unpaired


def test_the_report_lists_every_macro_with_its_source() -> None:
    mapping = _mapped(
        description="<BOT> ha {{random: 30, 40}} anni. <BOT> ride.",
        personality="{{// solo per l'autore}}Calma.",
        first_mes="Ciao {{roll: d20}}<USER>!",
    )

    assert mapping.draft.identity.biography == "{{char}} ha 30 anni. {{char}} ride."
    assert mapping.draft.identity.age == 30
    macros = _report(mapping)["macros"]
    listed = [(item["source"], item["macro"], item["replacement"], item["action"], item["count"]) for item in macros]
    assert listed == [
        ("data.description", "<BOT>", "{{char}}", "placeholder", 2),
        ("data.description", "{{random: 30, 40}}", "30", "first_option", 1),
        ("data.personality", "{{// solo per l'autore}}", "", "removed", 1),
        ("data.first_mes", "<USER>", "{{user}}", "placeholder", 1),
        ("data.first_mes", "{{roll: d20}}", "", "removed", 1),
    ]
    assert mapping.scenarios[0].opening_message == "Ciao {{user}}!"


def test_greetings_become_scenarios_only_when_asked() -> None:
    mapping = _fixture("v2.json")

    assert scenarios_to_create(mapping, create_scenarios=False) == []
    assert scenarios_to_create(mapping, create_scenarios=True) == mapping.scenarios
    assert len(mapping.scenarios) == 3


def test_a_scenario_text_over_the_limit_is_left_out_and_reported() -> None:
    mapping = _mapped(scenario="s" * 2001, first_mes="Ciao.", alternate_greetings=["", "g" * 2001, "Ehi."])

    assert [(item.title, item.description, item.opening_message) for item in mapping.scenarios] == [
        ("Mara", "", "Ciao."),
        ("Mara (saluto 4)", "", "Ehi."),
    ]
    left = [(item["source"], item["reason"]) for item in _report(mapping)["left_out"]]
    assert left == [
        ("data.scenario", "scenario_too_long"),
        ("data.alternate_greetings[1]", "scenario_too_long"),
    ]


def test_a_name_over_the_limit_is_left_out_and_reported() -> None:
    mapping = _mapped(name="  " + "N" * 81 + "  ")

    assert mapping.draft.identity.first_name == ""
    assert "identity.first_name" not in mapping.card_fields
    assert _report(mapping)["left_out"][0]["reason"] == "name_too_long"


def test_the_imported_draft_mirrors_the_character_draft() -> None:
    assert list(ImportedIdentity.model_fields) == list(Identity.model_fields)
    assert list(ImportedDraft.model_fields) == list(CharacterDraft.model_fields)


# T-CRD-04, the age part


@pytest.mark.parametrize(
    ("description", "age"),
    [
        ("She is 27 years old and restless.", 27),
        ("A 27-year-old courier.", 27),
        ("Una donna di 27 anni.", 27),
        ("Age: 27\nJob: courier", 27),
        ("age:27", 27),
    ],
)
def test_an_explicit_adult_age_fills_the_age_from_the_card(description: str, age: int) -> None:
    mapping = _mapped(description=description)

    assert mapping.draft.identity.age == age
    assert "identity.age" in mapping.card_fields
    assert _report(mapping)["age"] == {"status": "stated", "value": age, "source": "data.description"}


@pytest.mark.parametrize(
    ("description", "status"),
    [
        ("A young courier with an old bicycle.", "not_stated"),
        ("Lavora al porto da 20 anni.", "not_stated"),
        ("Si è trasferita 5 anni fa.", "not_stated"),
        ("In her twenties.", "not_stated"),
        ("She is 30 years old; her brother is 40 years old.", "conflicting"),
        ("Age: 150", "not_adult"),
    ],
)
def test_the_age_stays_empty_without_one_explicit_adult_age(description: str, status: str) -> None:
    mapping = _mapped(description=description)

    assert mapping.draft.identity.age is None
    assert "identity.age" not in mapping.card_fields
    assert _report(mapping)["age"]["status"] == status


def test_the_same_age_in_two_texts_is_one_age() -> None:
    mapping = _mapped(description="She is 33 years old.", personality="Age: 33")

    assert mapping.draft.identity.age == 33


# T-CRD-06: placeholders at prompt time, and the fields that never reach a prompt


class _WordRuntime:
    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        yield TextChunk("", "stop", "")

    async def generate(self, request: TextRequest) -> str:
        return ""

    async def abort(self, genkey: str) -> bool:
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=8192)


class _NoMemories:
    def retrieve_relevant_memories(self, query: RetrievalQuery) -> RetrievalResult:
        return RetrievalResult(items=(), token_cost=0, candidate_count=0)


def _message(seq: int, author: Author, content: str) -> Message:
    return Message(
        id=f"message-{seq}",
        conversation_id="chat",
        seq=seq,
        client_message_id=None,
        author=author,
        kind="text",
        status=MessageStatus.COMPLETE,
        read_state=None,
        active_variant_id=f"variant-{seq}",
        content=content,
        variant_count=1,
        edited_at=None,
        deleted_at=None,
        created_at="2026-11-27T10:00:00.000Z",
    )


def _imported_card() -> tuple[CharacterCard, DirectMapping]:
    mapping = _fixture("v2.json")
    return CharacterCard.model_validate(mapping.draft.model_dump(mode="json")), mapping


async def _build(card: CharacterCard, scenario: dict[str, object], count: int, *, proactive: bool = False) -> Assembly:
    messages = [
        _message(seq, Author.USER if seq % 2 else Author.CHARACTER, f"Messaggio {seq}.") for seq in range(1, count + 1)
    ]
    history, current = (messages, None) if proactive else (messages[:-1], messages[-1])
    request = ContextRequest(
        character_id="character",
        conversation_id="chat",
        card=card,
        persona=ANNA,
        language="it",
        shares_memory=False,
        history=history,
        current=current,
        summaries=(),
        now=NOW,
        scenario=scenario,
        instruction="Scrivi tu per primo." if proactive else "",
    )
    return await ContextBuilder(_WordRuntime(), _NoMemories()).build(request, formatter_for("gemma4"), 400)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_render_text_replaces_the_two_placeholders_in_any_case() -> None:
    assert render_text("{{char}} saluta {{USER}}; {{ Char }}.", "Livia", "Anna") == "Livia saluta Anna; Livia."
    assert render_text("Nessun segnaposto.", "Livia", "Anna") == "Nessun segnaposto."


@pytest.mark.anyio
@pytest.mark.parametrize("proactive", [False, True])
async def test_reply_and_proactive_prompts_render_the_placeholders_and_keep_the_card_only_fields_out(
    proactive: bool,
) -> None:
    card, mapping = _imported_card()
    first = mapping.scenarios[0]
    scenario: dict[str, object] = {"title": first.title, "description": first.description, "opening_message": ""}

    assembly = await _build(card, scenario, 5, proactive=proactive)
    text = assembly.prompt.text

    assert "{{" not in text
    assert "Livia Sarno is a 31 years old lighthouse keeper" in text
    assert "A storm strands Anna at the lighthouse for the night." in text
    assert "Anna: Does the light ever go out?\nLivia Sarno: Not while I am awake." in text
    for forbidden in NEVER_IN_A_PROMPT:
        assert forbidden not in text
    assert card.identity.biography.startswith("{{char}}"), "the card keeps its placeholders"


@pytest.mark.anyio
async def test_rendered_placeholders_keep_the_stable_prefix() -> None:
    """T-MOD-05 with an imported card: two consecutive replies share the stable text and the history."""
    card, mapping = _imported_card()
    scenario: dict[str, object] = {"title": "Livia Sarno", "description": mapping.scenarios[0].description}

    first = await _build(card, scenario, 5)
    second = await _build(card, scenario, 7)

    assert second.prompt.text.startswith(first.prompt.prefix)
    assert "Anna" in first.prompt.prefix


def test_the_draft_prompt_never_carries_the_card_only_fields() -> None:
    card, _ = _imported_card()
    shown = card_as_draft(card).model_dump(mode="json")
    prompt = character_draft.render_prompt("gemma4", "Rendila più ironica.", shown, None)

    for forbidden in NEVER_IN_A_PROMPT:
        assert forbidden not in prompt
