"""The direct mapping of a parsed character card to a draft for the Creator, with its import report (ADR 0047;
docs/character-system.md, Character cards).

Deterministic and offline: the same card always gives the same draft, and no model runs. The text stays in its source
language. Every mapped text first passes `expand_macros`, so it holds only the `{{char}}` and `{{user}}` placeholders,
which the prompt renders. The card fields that never reach a prompt (`system_prompt`, `post_history_instructions`,
`creator_notes` and the rest of the table) are not read here, so no draft and no prompt can carry them.
"""

import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from lyrveil.domain.card import (
    MAX_AGE,
    MAX_NAME_LENGTH,
    MIN_AGE,
    CommunicationProfile,
    ExampleExchange,
    GrammaticalGender,
    LanguageCode,
    Text,
)
from lyrveil.domain.card_draft import DraftTrait, DraftVisual
from lyrveil.domain.card_placeholders import MacroUse, expand_macros
from lyrveil.domain.scenarios import MAX_DESCRIPTION_CHARS, MAX_OPENING_CHARS, MAX_TITLE_CHARS
from lyrveil.domain.tavern_card import CardData

# The card's limits (docs/character-system.md, Card schema) that decide what the mapping leaves out.
MAX_TRAIT_CHARS = 500
MAX_EXAMPLES = 8
MAX_EXAMPLE_USER_CHARS = 300
MAX_EXAMPLE_CHARACTER_CHARS = 500

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")
_START = re.compile(r"<start>", re.IGNORECASE)
_SPEAKER = re.compile(r"^\s*\{\{\s*(char|user)\s*\}\}\s*:[ \t]?", re.IGNORECASE)

# The explicit forms of an age (ADR 0047): "27 years old" (or "27-year-old"), "27 anni" and "age: 27".
_YEARS_OLD = re.compile(r"(?<!\d)(\d{1,3})(?:\s+|\s*-\s*)years?(?:\s+|\s*-\s*)old\b", re.IGNORECASE)
_ANNI = re.compile(r"(?<!\d)(\d{1,3})\s+anni\b", re.IGNORECASE)
_AGE_LABEL = re.compile(r"\bage\s*:\s*(\d{1,3})(?!\d)", re.IGNORECASE)
# "N anni" also says how long or how long ago: "da 10 anni", "per 3 anni", "tra 2 anni", "5 anni fa" state no age.
_DURATION_BEFORE = re.compile(r"\b(?:da|per|dopo|tra|fra)\s+$", re.IGNORECASE)
_AGO_AFTER = re.compile(r"\s+fa\b", re.IGNORECASE)
# The texts that can state the character's age, in the order the source is named in the report.
_AGE_FIELDS = ("description", "personality", "scenario")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImportedIdentity(_Strict):
    """The identity of a direct draft: Identity's fields, except that the name may be empty (the card's was blank or too
    long) and the age is None unless the card states an adult age. The Creator requires both before a save, and
    tests/unit/test_card_mapping.py fails when the fields stop mirroring Identity."""

    first_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_NAME_LENGTH)] = ""
    last_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_NAME_LENGTH)] | None = None
    age: Annotated[int, Field(ge=MIN_AGE, le=MAX_AGE)] | None = None
    grammatical_gender: GrammaticalGender = "non_indicato"
    narrative_identity: Text = ""
    biography: Text = ""
    occupation: Text = ""
    interests: list[Text] = []
    background: Text = ""
    setting: Text = ""
    primary_language: LanguageCode = "it"


class ImportedDraft(_Strict):
    """A CharacterDraft whose identity is an ImportedIdentity: what **Apri nel Creator** opens."""

    identity: ImportedIdentity
    traits: list[DraftTrait] = []
    communication: CommunicationProfile = Field(default_factory=CommunicationProfile)
    visual: DraftVisual = Field(default_factory=DraftVisual)


@dataclass(frozen=True)
class ScenarioProposal:
    """A scenario the import offers from a greeting; `source` is the card path of its opening message, or of the
    scenario text when the card has no first greeting. Within the scenario limits."""

    title: str
    description: str
    opening_message: str
    source: str


@dataclass(frozen=True)
class LeftOut:
    """A card text the mapping did not use. `item` is the 1-based number of the `<START>` block for an example, else
    None. `reason` is one of the LEFT_OUT_DETAILS keys."""

    source: str
    target: str
    reason: str
    item: int | None = None

    def as_json(self) -> dict[str, Any]:
        return {**asdict(self), "detail": LEFT_OUT_DETAILS[self.reason]}


LEFT_OUT_DETAILS = {
    "name_empty": "La card non ha un nome: scrivilo nel Creator.",
    "name_too_long": f"Il nome supera {MAX_NAME_LENGTH} caratteri: scrivilo nel Creator.",
    "no_character_line": "Il blocco di esempio non ha una battuta del personaggio.",
    "example_user_too_long": f"Il messaggio dell'utente supera {MAX_EXAMPLE_USER_CHARS} caratteri.",
    "example_character_too_long": f"La risposta del personaggio supera {MAX_EXAMPLE_CHARACTER_CHARS} caratteri.",
    "too_many_examples": f"Sono tenuti al massimo {MAX_EXAMPLES} esempi.",
    "scenario_too_long": f"Il testo supera il limite di {MAX_DESCRIPTION_CHARS} caratteri dello scenario.",
}


@dataclass(frozen=True)
class AgeFinding:
    """What the mapping read of the age. `status` is `stated` (one explicit adult age, in `value`), `not_stated`,
    `conflicting` (explicit ages that differ) or `not_adult` (one explicit age outside 18 to 120). `source` is the card
    path of the first explicit age, or None without one."""

    status: str
    value: int | None
    source: str | None


@dataclass(frozen=True)
class DirectMapping:
    """The direct draft, the dotted paths of the fields it took from the card (marked **dalla card**), the scenarios it
    offers from the greetings, and the report: every macro replaced per source path, every text left out, the age."""

    draft: ImportedDraft
    card_fields: list[str]
    scenarios: list[ScenarioProposal]
    macros: list[tuple[str, MacroUse]]
    left_out: list[LeftOut]
    age: AgeFinding

    def draft_json(self) -> dict[str, Any]:
        """Returns the `direct_draft` of the import answer: `draft`, `card_fields` and `scenarios`."""
        return {
            "draft": self.draft.model_dump(mode="json"),
            "card_fields": list(self.card_fields),
            "scenarios": [asdict(scenario) for scenario in self.scenarios],
        }

    def report_json(self) -> dict[str, Any]:
        """Returns the `report` of the import answer. `macros` groups equal replacements of one source path, with
        their `count`, in the order they first appear."""
        grouped: dict[tuple[str, str, str, str], int] = {}
        for source, use in self.macros:
            key = (source, use.macro, use.replacement, use.action.value)
            grouped[key] = grouped.get(key, 0) + 1
        return {
            "macros": [
                {"source": source, "macro": macro, "replacement": replacement, "action": action, "count": count}
                for (source, macro, replacement, action), count in grouped.items()
            ],
            "left_out": [item.as_json() for item in self.left_out],
            "age": asdict(self.age),
        }


def scenarios_to_create(mapping: DirectMapping, create_scenarios: bool) -> list[ScenarioProposal]:
    """Returns the scenarios a save writes: those the mapping offers when the user asked for them (**Crea gli scenari
    dai saluti**), otherwise none."""
    return list(mapping.scenarios) if create_scenarios else []


@dataclass
class _Run:
    prefix: str
    macros: list[tuple[str, MacroUse]] = field(default_factory=list)
    left_out: list[LeftOut] = field(default_factory=list)

    def path(self, name: str) -> str:
        return f"{self.prefix}.{name}" if self.prefix else name

    def text(self, value: str, name: str) -> str:
        """Returns a card text with its line ends made `\\n` and its macros applied, which the report records."""
        expanded, uses = expand_macros(value.replace("\r\n", "\n").replace("\r", "\n"))
        self.macros.extend((self.path(name), use) for use in uses)
        return expanded.strip()


def map_card(data: CardData, *, prefix: str = "data", language: LanguageCode = "it") -> DirectMapping:
    """Returns the direct mapping of a parsed card (docs/character-system.md, Direct mapping).

    `prefix` is where the card's fields sit in the stored source: `data` for V2 and V3, "" for V1; the report names
    every source by its full path, such as `data.mes_example`. `language` becomes `identity.primary_language`. The
    communication profile keeps its defaults apart from the examples, and the visual profile stays empty.
    """
    run = _Run(prefix)
    name = data.name.strip()
    first_name = name
    if not name:
        run.left_out.append(LeftOut(run.path("name"), "identity.first_name", "name_empty"))
    elif len(name) > MAX_NAME_LENGTH:
        run.left_out.append(LeftOut(run.path("name"), "identity.first_name", "name_too_long"))
        first_name = ""
    texts = {source: run.text(getattr(data, source), source) for source in _AGE_FIELDS}
    traits = [DraftTrait(category="main_trait", description=part) for part in _trait_parts(texts["personality"])]
    examples = _examples(run, run.text(data.mes_example, "mes_example"))
    age = _age(run, texts)
    scenarios = _scenarios(run, name, texts["scenario"], data)
    draft = ImportedDraft(
        identity=ImportedIdentity(
            first_name=first_name, age=age.value, biography=texts["description"], primary_language=language
        ),
        traits=traits,
        communication=CommunicationProfile(examples=examples),
    )
    filled = (
        ("identity.first_name", bool(first_name)),
        ("identity.age", age.value is not None),
        ("identity.biography", bool(draft.identity.biography)),
        ("traits", bool(traits)),
        ("communication.examples", bool(examples)),
    )
    return DirectMapping(
        draft=draft,
        card_fields=[path for path, used in filled if used],
        scenarios=scenarios,
        macros=run.macros,
        left_out=run.left_out,
        age=age,
    )


def _trait_parts(personality: str) -> Iterator[str]:
    """Yields one trait per paragraph. A paragraph over MAX_TRAIT_CHARS is cut into runs of whole sentences that fit;
    a single sentence over it is cut at spaces, and a word over it at the limit, so no text is lost."""
    for paragraph in _PARAGRAPH_BREAK.split(personality):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= MAX_TRAIT_CHARS:
            yield paragraph
            continue
        yield from _packed(_SENTENCE_BREAK.split(paragraph), MAX_TRAIT_CHARS)


def _packed(pieces: list[str], limit: int) -> Iterator[str]:
    """Yields the pieces joined by spaces into runs of at most `limit` characters, in order."""
    current = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) > limit:
            if current:
                yield current
                current = ""
            words = piece.split()
            if len(words) > 1:
                yield from _packed(words, limit)
            else:
                yield from (piece[start : start + limit] for start in range(0, len(piece), limit))
            continue
        joined = f"{current} {piece}" if current else piece
        if len(joined) <= limit:
            current = joined
        else:
            yield current
            current = piece
    if current:
        yield current


def _examples(run: _Run, text: str) -> list[ExampleExchange]:
    """Returns one pair per non-blank `<START>` block, up to MAX_EXAMPLES: the first `{{user}}:` turn, and every
    `{{char}}:` turn joined by line breaks. A line without a speaker continues the turn above it; lines before any
    speaker are ignored. A block with no user turn gives `user` None. Blocks over the limits are reported."""
    source = run.path("mes_example")
    kept: list[ExampleExchange] = []
    blocks = [block for block in _START.split(text) if block.strip()]
    for number, block in enumerate(blocks, start=1):
        user: list[str] | None = None
        character: list[str] = []
        turn: list[str] | None = None
        for line in block.split("\n"):
            speaker = _SPEAKER.match(line)
            if speaker is None:
                if turn is not None:
                    turn.append(line)
                continue
            rest = line[speaker.end() :]
            if speaker.group(1).lower() == "char":
                character.append(rest)
                turn = character
            elif user is None:
                user = [rest]
                turn = user
            else:
                turn = []  # A later user turn is not part of the pair.
        user_text = _lines(user or [])
        character_text = _lines(character)
        reason = None
        if not character_text:
            reason = "no_character_line"
        elif len(user_text) > MAX_EXAMPLE_USER_CHARS:
            reason = "example_user_too_long"
        elif len(character_text) > MAX_EXAMPLE_CHARACTER_CHARS:
            reason = "example_character_too_long"
        elif len(kept) == MAX_EXAMPLES:
            reason = "too_many_examples"
        if reason is not None:
            run.left_out.append(LeftOut(source, "communication.examples", reason, number))
            continue
        kept.append(ExampleExchange(user=user_text or None, character=character_text))
    return kept


def _lines(lines: list[str]) -> str:
    return "\n".join(line.strip() for line in lines if line.strip())


def _age(run: _Run, texts: dict[str, str]) -> AgeFinding:
    """Reads the age only from an explicit form, never guessed: one distinct value across the texts, from 18 to 120,
    fills it; none, several or one outside that range leave it empty."""
    values: list[int] = []
    first_source: str | None = None
    for name in _AGE_FIELDS:
        found = list(_explicit_ages(texts[name]))
        if found and first_source is None:
            first_source = run.path(name)
        values.extend(found)
    distinct = set(values)
    if not distinct:
        return AgeFinding("not_stated", None, None)
    if len(distinct) > 1:
        return AgeFinding("conflicting", None, first_source)
    (value,) = distinct
    if not MIN_AGE <= value <= MAX_AGE:
        return AgeFinding("not_adult", None, first_source)
    return AgeFinding("stated", value, first_source)


def _explicit_ages(text: str) -> Iterator[int]:
    for match in _YEARS_OLD.finditer(text):
        yield int(match.group(1))
    for match in _AGE_LABEL.finditer(text):
        yield int(match.group(1))
    for match in _ANNI.finditer(text):
        before = text[max(0, match.start() - 8) : match.start()]
        if _DURATION_BEFORE.search(before) or _AGO_AFTER.match(text, match.end()):
            continue
        yield int(match.group(1))


def _scenarios(run: _Run, name: str, description: str, data: CardData) -> list[ScenarioProposal]:
    """Returns the scenario of `scenario` with `first_mes`, titled with the name, then one per non-blank alternate
    greeting with the same description, titled `<name> (saluto N)` where N is its place among the greetings (2 for the
    first alternate). A text over the scenario limit is left out and reported; an alternate greeting left out gives no
    scenario, and the first scenario needs a description or an opening message."""
    if len(description) > MAX_DESCRIPTION_CHARS:
        run.left_out.append(LeftOut(run.path("scenario"), "scenario.description", "scenario_too_long"))
        description = ""
    proposals: list[ScenarioProposal] = []
    opening = run.text(data.first_mes, "first_mes")
    if len(opening) > MAX_OPENING_CHARS:
        run.left_out.append(LeftOut(run.path("first_mes"), "scenario.opening_message", "scenario_too_long"))
        opening = ""
    if description or opening:
        source = run.path("first_mes" if opening else "scenario")
        proposals.append(ScenarioProposal(_title(name, ""), description, opening, source))
    for index, greeting in enumerate(data.alternate_greetings):
        path = f"alternate_greetings[{index}]"
        text = run.text(greeting, path)
        if not text:
            continue
        if len(text) > MAX_OPENING_CHARS:
            run.left_out.append(LeftOut(run.path(path), "scenario.opening_message", "scenario_too_long"))
            continue
        proposals.append(ScenarioProposal(_title(name, f" (saluto {index + 2})"), description, text, run.path(path)))
    return proposals


def _title(name: str, suffix: str) -> str:
    base = (name or "Scenario")[: MAX_TITLE_CHARS - len(suffix)].rstrip()
    return f"{base}{suffix}"
