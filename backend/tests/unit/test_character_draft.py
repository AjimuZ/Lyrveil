"""The draft schema and its grammar: CharacterDraft mirrors CharacterCard, the grammar generated from it accepts exactly
the drafts of that schema, and a draft over an existing card keeps what the card locks (T-CHR-09, T-CHR-10 support).

The grammar is checked by a small GBNF matcher for the subset schema_to_gbnf writes, so a drift between the schema and
the grammar fails here without a model.
"""

import json
import re
from collections.abc import Iterable, Mapping
from functools import cache
from typing import Any

import pytest
from pydantic import BaseModel

from lyrveil.domain.card import CharacterCard
from lyrveil.domain.card_draft import (
    DRAFT_EXCLUDED_FIELDS,
    CharacterDraft,
    card_as_draft,
    draft_to_raw_card,
    proposed_fields,
)
from lyrveil.domain.card_locks import keep_locked
from lyrveil.prompts.character_draft import EXAMPLE_DRAFT
from lyrveil.prompts.gbnf import UnsupportedSchemaError, schema_to_gbnf
from lyrveil.services.card_rules import validate_card

# A GBNF matcher for the constructs schema_to_gbnf writes: literals, character classes, rule names, groups,
# alternatives and the ?, * and + suffixes. It returns every end position a node can reach, so it needs no backtracking.

Node = tuple[Any, ...]
_TOKEN = re.compile(r'\s*(?:("(?:[^"\\]|\\.)*")|(\[(?:[^\]\\]|\\.)*\])|([a-z0-9-]+)|([()|?*+]))')
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "]": "]", "[": "[", "^": "^", "-": "-"}


def _unescape(body: str) -> list[str]:
    chars: list[str] = []
    index = 0
    while index < len(body):
        if body[index] == "\\":
            code = body[index + 1]
            if code == "x":
                chars.append(chr(int(body[index + 2 : index + 4], 16)))
                index += 4
                continue
            chars.append(_ESCAPES[code])
            index += 2
            continue
        chars.append(body[index])
        index += 1
    return chars


def _char_class(text: str) -> Node:
    body = text[1:-1]
    negated = body.startswith("^")
    # Escapes are resolved first; a "-" between two characters is a range. The writer never escapes a range dash.
    raw = body[1:] if negated else body
    tokens: list[tuple[str, bool]] = []
    index = 0
    while index < len(raw):
        if raw[index] == "\\":
            end = index + 4 if raw[index + 1] == "x" else index + 2
            tokens.append(("".join(_unescape(raw[index:end])), True))
            index = end
        else:
            tokens.append((raw[index], False))
            index += 1
    ranges: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        if index + 2 < len(tokens) and tokens[index + 1] == ("-", False):
            ranges.append((tokens[index][0], tokens[index + 2][0]))
            index += 3
        else:
            ranges.append((tokens[index][0], tokens[index][0]))
            index += 1
    return ("class", negated, tuple(ranges))


def _parse_expression(tokens: list[tuple[str, str]], start: int) -> tuple[Node, int]:
    alternatives: list[Node] = []
    sequence: list[Node] = []
    index = start
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "op" and value == ")":
            break
        if kind == "op" and value == "|":
            alternatives.append(("seq", tuple(sequence)))
            sequence = []
            index += 1
            continue
        if kind == "op" and value == "(":
            node, index = _parse_expression(tokens, index + 1)
            index += 1
        elif kind == "literal":
            node, index = ("literal", "".join(_unescape(value[1:-1]))), index + 1
        elif kind == "class":
            node, index = _char_class(value), index + 1
        else:
            node, index = ("rule", value), index + 1
        while index < len(tokens) and tokens[index] in {("op", "?"), ("op", "*"), ("op", "+")}:
            node = (tokens[index][1], node)
            index += 1
        sequence.append(node)
    alternatives.append(("seq", tuple(sequence)))
    return ("alt", tuple(alternatives)), index


def parse_grammar(grammar: str) -> dict[str, Node]:
    rules: dict[str, Node] = {}
    for line in grammar.strip().splitlines():
        name, body = line.split(" ::= ", 1)
        tokens: list[tuple[str, str]] = []
        position = 0
        while body[position:].strip():
            found = _TOKEN.match(body, position)
            assert found is not None, f"cannot read {body[position:]!r}"
            literal, char_class, word, op = found.groups()
            if literal:
                tokens.append(("literal", literal))
            elif char_class:
                tokens.append(("class", char_class))
            elif word:
                tokens.append(("rule", word))
            else:
                tokens.append(("op", op))
            position = found.end()
        rules[name], _ = _parse_expression(tokens, 0)
    return rules


def _union(sets: Iterable[frozenset[int]]) -> frozenset[int]:
    joined: set[int] = set()
    for positions in sets:
        joined |= positions
    return frozenset(joined)


def matches(grammar: str, text: str) -> bool:
    """Tells whether the whole of `text` is a sentence of the grammar's `root`."""
    rules = parse_grammar(grammar)

    @cache
    def rule(name: str, position: int) -> frozenset[int]:
        return ends(rules[name], position)

    def ends(node: Node, position: int) -> frozenset[int]:
        kind = node[0]
        if kind == "literal":
            return frozenset({position + len(node[1])}) if text.startswith(node[1], position) else frozenset()
        if kind == "class":
            if position >= len(text):
                return frozenset()
            inside = any(low <= text[position] <= high for low, high in node[2])
            return frozenset({position + 1}) if inside != node[1] else frozenset()
        if kind == "rule":
            return rule(node[1], position)
        if kind == "seq":
            reached = frozenset({position})
            for child in node[1]:
                reached = _union(ends(child, at) for at in reached)
            return reached
        if kind == "alt":
            return _union(ends(child, position) for child in node[1])
        if kind == "?":
            return frozenset({position}) | ends(node[1], position)
        reached = frozenset({position}) if kind == "*" else ends(node[1], position)
        frontier = reached
        while frontier:
            frontier = _union(ends(node[1], at) for at in frontier) - reached
            reached |= frontier
        return reached

    return len(text) in rule("root", 0)


GRAMMAR = schema_to_gbnf(CharacterDraft)


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def full_draft(**identity: object) -> dict[str, Any]:
    """Returns a draft with every key present, as the grammar writes it."""
    draft = CharacterDraft.model_validate(EXAMPLE_DRAFT).model_dump(mode="json")
    draft["identity"].update(identity)
    return draft


# The card fields a draft must mirror: every path of the card schema, with the draft's own schema at that path.


def _resolve(schema: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any]:
    if "$ref" in schema:
        return _resolve(root["$defs"][schema["$ref"].split("/")[-1]], root)
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _resolve(schema["allOf"][0], root)
    return schema


def _leaves(schema: Mapping[str, Any], root: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Returns each field path with its schema, nested objects and the items of object lists walked into."""
    schema = _resolve(schema, root)
    if schema.get("type") == "array" and "properties" in _resolve(schema.get("items", {}), root):
        bounds = {key: schema[key] for key in ("minItems", "maxItems") if key in schema}
        return {prefix: bounds, **_leaves(schema["items"], root, prefix)}
    if "properties" not in schema:
        return {prefix: schema}
    flat: dict[str, Any] = {}
    for key, inner in schema["properties"].items():
        flat.update(_leaves(inner, root, f"{prefix}.{key}" if prefix else key))
    return flat


def _paths(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    return {path: value for path, value in _leaves(schema, schema).items() if path}


def test_the_draft_has_every_card_field_but_locks_seeds_and_english_tags_with_the_same_rules() -> None:
    card, draft = _paths(CharacterCard), _paths(CharacterDraft)
    excluded = {
        path for path in card if any(path == name or path.startswith(f"{name}.") for name in DRAFT_EXCLUDED_FIELDS)
    }
    assert excluded, "the exclusions must name card fields"
    assert set(draft) == set(card) - excluded
    assert {path: draft[path] for path in draft} == {path: card[path] for path in draft}


def test_the_grammar_accepts_a_whole_draft_and_the_worked_example() -> None:
    assert matches(GRAMMAR, compact(full_draft()))
    assert matches(GRAMMAR, json.dumps(full_draft(last_name=None), ensure_ascii=False))
    assert matches(GRAMMAR, compact(full_draft(first_name='Lia "la Rossa"\\')))


def test_the_grammar_refuses_an_unknown_or_missing_key_a_minor_and_a_closed_value_outside_its_list() -> None:
    unknown = full_draft()
    unknown["visual"]["locked_fields"] = ["hair"]
    missing = full_draft()
    del missing["communication"]["emoji"]
    closed = full_draft()
    closed["communication"]["formality"] = "solenne"
    too_many = full_draft()
    too_many["visual"]["outfits"] = [{"key": f"abito_{n}", "label": "Abito", "description": ""} for n in range(6)]
    for refused in (unknown, missing, closed, too_many, full_draft(age=17), full_draft(age=121)):
        assert not matches(GRAMMAR, compact(refused))
    assert not matches(GRAMMAR, compact(full_draft())[:-1])


def test_the_grammar_is_deterministic_and_refuses_a_schema_outside_the_subset() -> None:
    class Scores(BaseModel):
        weights: dict[str, float]

    assert schema_to_gbnf(CharacterDraft) == GRAMMAR
    assert GRAMMAR.startswith("root ::= ")
    with pytest.raises(UnsupportedSchemaError):
        schema_to_gbnf(Scores)


def test_the_worked_example_is_a_valid_draft_and_card() -> None:
    draft = CharacterDraft.model_validate(EXAMPLE_DRAFT)
    assert validate_card(draft_to_raw_card(draft, None)).identity.age == 34


CURRENT = validate_card(
    {
        "identity": {"first_name": "Nerea", "age": 27},
        "traits": [
            {"category": "main_trait", "description": "Ironica.", "locked": True},
            {"category": "fear", "description": "Perdersi.", "locked": False},
        ],
        "visual": {
            "hair": "Capelli rossi corti.",
            "identity_en": {"hair_color": "red hair", "eye_color": "green eyes"},
            "outfits": [{"key": "festa", "label": "Festa", "tags_en": ["blue dress"]}],
            "locked_fields": ["hair"],
            "seed_policy": "fixed",
            "base_seed": 42,
        },
    }
)


def test_a_draft_over_a_card_keeps_its_locks_seed_and_english_tags_and_names_what_it_changed() -> None:
    proposed = card_as_draft(CURRENT).model_dump(mode="json")
    proposed["traits"] = [{"category": "humor", "description": "Battute asciutte."}]
    proposed["visual"]["hair"] = "Capelli neri lunghi."
    proposed["visual"]["eyes"] = "Occhi grigi."
    proposed["visual"]["outfits"].append({"key": "viaggio", "label": "Viaggio", "description": ""})
    draft = CharacterDraft.model_validate(proposed)

    card = keep_locked(CURRENT, validate_card(draft_to_raw_card(draft, CURRENT)))

    assert [(trait.description, trait.locked) for trait in card.traits] == [
        ("Ironica.", True),
        ("Battute asciutte.", False),
    ]
    assert (card.visual.hair, card.visual.eyes) == ("Capelli rossi corti.", "Occhi grigi.")
    assert card.visual.identity_en.hair_color == "red hair"
    assert (card.visual.seed_policy, card.visual.base_seed, card.visual.locked_fields) == ("fixed", 42, ["hair"])
    assert [outfit.tags_en for outfit in card.visual.outfits] == [["blue dress"], []]
    assert proposed_fields(card, CURRENT) == ["traits", "visual.eyes", "visual.outfits"]


def test_a_new_draft_names_the_required_fields_and_every_field_it_filled() -> None:
    card = validate_card(draft_to_raw_card(CharacterDraft.model_validate(EXAMPLE_DRAFT), None))
    fields = proposed_fields(card, None)
    assert fields[:2] == ["identity.first_name", "identity.age"]
    assert {"identity.biography", "traits", "communication.examples", "visual.subject_tag"} <= set(fields)
    assert "communication.formality" not in fields, "a value equal to the default is not proposed"
