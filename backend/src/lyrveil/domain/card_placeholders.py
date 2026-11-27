"""The `{{char}}` and `{{user}}` placeholders and the card macros of ADR 0047: what an import keeps, removes or replaces
in a card text, and how a prompt renders the two placeholders.

Pure: no database, no setting, no runtime. An import calls `expand_macros` once, so the stored text holds only the two
placeholders and the reply prompt stays deterministic, which keeps the stable prefix stable (ADR 0031). A prompt calls
`render_card` with the character's first name and the user's display name.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel

from lyrveil.domain.card import CharacterCard

CHAR = "{{char}}"
USER = "{{user}}"

# The two placeholders, matched without case and with blanks inside the braces, as writers type them.
_PLACEHOLDER = re.compile(r"\{\{\s*(char|user)\s*\}\}", re.IGNORECASE)
_KEPT = re.compile(r"\s*(char|user)\s*", re.IGNORECASE)
# The legacy forms that older cards use for the two placeholders.
_LEGACY = re.compile(r"<(bot|user|char)>", re.IGNORECASE)
_LEGACY_TARGETS = {"bot": CHAR, "char": CHAR, "user": USER}
_TOKEN = re.compile(r"\{\{|\}\}")
# Deeper macros than this are removed whole rather than evaluated, so a crafted text cannot exhaust the stack.
MAX_MACRO_DEPTH = 16
# How much of a macro the report quotes.
MAX_QUOTED_CHARS = 200


class MacroAction(StrEnum):
    """What an import did with a macro, as the report names it."""

    PLACEHOLDER = "placeholder"  # A legacy form became {{char}} or {{user}}.
    REMOVED = "removed"  # Comments, hidden keys, `roll` and every unknown macro.
    FIRST_OPTION = "first_option"  # `random` and `pick` keep their first option.
    REVERSED = "reversed"  # `reverse` is evaluated.


@dataclass(frozen=True)
class MacroUse:
    """One macro an import replaced: its text with the legacy forms already turned into placeholders, what it became,
    and why. Both texts are cut at MAX_QUOTED_CHARS for the report; the expanded text itself is never cut."""

    macro: str
    replacement: str
    action: MacroAction


@dataclass
class _Node:
    """A matched `{{ ... }}`: `start` is the index of its `{{`, `end` the index of its `}}`."""

    start: int
    end: int = -1
    children: list["_Node"] = field(default_factory=list)


def expand_macros(text: str) -> tuple[str, list[MacroUse]]:
    """Returns `text` with the macros of ADR 0047 applied, and every replacement in text order.

    `<BOT>`, `<USER>` and `<char>`, in any case, become `{{char}}` and `{{user}}`. `{{char}}` and `{{user}}`, in any
    case, stay as written and are not reported. Of the other `{{ ... }}`: `//` comments, `comment:` and `hidden_key:`
    are removed; `random:` and `pick:` keep their first option (options split at `::` when there is one, else at `,`);
    `reverse:` gives its text reversed; `roll:` and any other macro are removed. Inner macros are applied before the
    one around them, and a macro nested deeper than MAX_MACRO_DEPTH is removed whole. A `{{` or `}}` without its pair
    stays as text. The pairs are found in one pass, so a text full of unpaired braces costs no more than its length.
    """
    uses: list[MacroUse] = []

    def legacy(match: re.Match[str]) -> str:
        replacement = _LEGACY_TARGETS[match.group(1).lower()]
        uses.append(MacroUse(match.group(0), replacement, MacroAction.PLACEHOLDER))
        return replacement

    text = _LEGACY.sub(legacy, text)
    roots = _pairs(text)
    if not roots:
        return text, uses

    def render(node: _Node, depth: int) -> str:
        raw = text[node.start : node.end + 2]
        if depth > MAX_MACRO_DEPTH:
            uses.append(MacroUse(_quoted(raw), "", MacroAction.REMOVED))
            return ""
        body = join(node.start + 2, node.end, node.children, depth + 1)
        if _KEPT.fullmatch(body):
            return raw
        replacement, action = _evaluate(body)
        uses.append(MacroUse(_quoted(raw), _quoted(replacement), action))
        return replacement

    def join(start: int, end: int, nodes: list[_Node], depth: int) -> str:
        parts: list[str] = []
        position = start
        for node in nodes:
            parts.append(text[position : node.start])
            parts.append(render(node, depth))
            position = node.end + 2
        parts.append(text[position:end])
        return "".join(parts)

    return join(0, len(text), roots, 1), uses


def _pairs(text: str) -> list[_Node]:
    """Returns the outermost matched `{{ ... }}` of `text` in order, each with its inner ones in order. One
    left-to-right pass with a stack: a `}}` closes the newest open `{{`, a `}}` with none open is text, and a `{{` never
    closed is text."""
    roots: list[_Node] = []
    stack: list[_Node] = []
    for token in _TOKEN.finditer(text):
        if token.group(0) == "{{":
            stack.append(_Node(token.start()))
        elif stack:
            node = stack.pop()
            node.end = token.start()
            (stack[-1].children if stack else roots).append(node)
    # An opener left open is text: its pairs move to the opener below it, or to the roots. They all start after what
    # that list already holds, so the order stays.
    while stack:
        orphan = stack.pop()
        (stack[-1].children if stack else roots).extend(orphan.children)
    return roots


def _evaluate(body: str) -> tuple[str, MacroAction]:
    """Returns what a macro other than the two placeholders becomes, and the action: `body` is its text between the
    braces, inner macros already applied."""
    stripped = body.strip()
    if stripped.startswith("//"):
        return "", MacroAction.REMOVED
    name, colon, argument = stripped.partition(":")
    if not colon:
        return "", MacroAction.REMOVED
    name = name.strip().lower()
    if name in ("random", "pick"):
        return _first_option(argument), MacroAction.FIRST_OPTION
    if name == "reverse":
        return argument.strip()[::-1], MacroAction.REVERSED
    return "", MacroAction.REMOVED


def _first_option(argument: str) -> str:
    # `{{random::a::b}}` leaves ":a::b" after the name's colon; `{{random: a, b}}` uses commas.
    if argument.startswith(":"):
        argument = argument[1:]
    options = argument.split("::") if "::" in argument else argument.split(",")
    return options[0].strip()


def _quoted(raw: str) -> str:
    return raw if len(raw) <= MAX_QUOTED_CHARS else raw[: MAX_QUOTED_CHARS - 1] + "…"


def render_text(text: str, first_name: str, user_name: str) -> str:
    """Returns `text` with `{{char}}` replaced by `first_name` and `{{user}}` by `user_name`, both matched without
    case. Nothing else changes, so the same inputs always give the same bytes."""
    if "{{" not in text:
        return text
    return _PLACEHOLDER.sub(lambda match: first_name if match.group(1).lower() == "char" else user_name, text)


def render_card(card: CharacterCard, user_name: str) -> CharacterCard:
    """Returns a copy of `card` whose every text has the two placeholders rendered with the card's first name and
    `user_name`. `card` does not change. The copy is not validated again: a rendered text may be longer than its field
    allows, and the copy only feeds a prompt, never storage."""
    first_name = card.identity.first_name
    return _rendered(card, lambda text: render_text(text, first_name, user_name))


def _rendered[Model: BaseModel](value: Model, render: Callable[[str], str]) -> Model:
    update = {name: _rendered_value(getattr(value, name), render) for name in type(value).model_fields}
    return value.model_copy(update=update)


def _rendered_value(value: object, render: Callable[[str], str]) -> object:
    if isinstance(value, str):
        return render(value)
    if isinstance(value, BaseModel):
        return _rendered(value, render)
    if isinstance(value, list):
        return [_rendered_value(item, render) for item in value]
    return value
