"""The tag sanitizer of docs/image-generation.md (Sanitizer), and the syntax check of prompts edited by the user."""

import math
import re

from lyrveil.domain.errors import PromptSyntaxRefusedError

# Everything the prompt syntax of ComfyUI could read as a weight, an escape, an embedding or a separator is dropped.
_EMBEDDING = re.compile(r"\S*embedding:\S*")
_DISALLOWED = re.compile(r"[^\w \-'.]", re.UNICODE)
_SPACES = re.compile(r" +")
# The weights a user may write in the advanced dialog (ADR 0035), bounds included.
LOWEST_USER_WEIGHT = 0.5
HIGHEST_USER_WEIGHT = 1.5


def sanitize_tag(raw: str) -> str:
    """Returns `raw` as a safe tag, or an empty string when nothing is left.

    Steps, in order: lowercase, trim and turn `_` into a space; drop the word `embedding:` with anything joined to it;
    keep only letters, digits, spaces, hyphens, apostrophes and periods; collapse spaces. The result never holds
    parentheses, colons, brackets, backslashes or commas, so no tag can change a weight or split itself. The function
    is idempotent.
    """
    text = raw.lower().strip().replace("_", " ")
    text = _EMBEDDING.sub(" ", text)
    text = _DISALLOWED.sub("", text)
    return _SPACES.sub(" ", text).strip()


def _top_level_groups(text: str) -> list[str]:
    """Splits balanced `text` into plain runs and whole top-level `( ... )` groups, as ComfyUI's parse_parentheses."""
    parts: list[str] = []
    current = ""
    depth = 0
    for char in text:
        if char == "(" and depth == 0:
            if current:
                parts.append(current)
            current = "("
            depth = 1
        elif char == "(":
            current += char
            depth += 1
        elif char == ")" and depth == 1:
            parts.append(current + ")")
            current = ""
            depth = 0
        elif char == ")":
            current += char
            depth -= 1
        else:
            current += char
    if current:
        parts.append(current)
    return parts


def _weights(text: str, weight: float) -> list[tuple[str, float]]:
    """Returns the segments of balanced `text` with their weights, by the rules of ComfyUI's token_weights: a group
    multiplies the weight around it by 1.1, and a group ending in `:number` sets its weight to that number instead."""
    segments: list[tuple[str, float]] = []
    for part in _top_level_groups(text):
        if len(part) < 2 or part[0] != "(" or part[-1] != ")":
            segments.append((part, weight))
            continue
        inner, inner_weight = part[1:-1], weight * 1.1
        colon = inner.rfind(":")
        if colon > 0:
            try:
                inner_weight = float(inner[colon + 1 :])
                inner = inner[:colon]
            except ValueError:
                pass
        segments += _weights(inner, inner_weight)
    return segments


def prompt_weights(text: str) -> list[tuple[str, float]]:
    """Returns the segments of a user prompt with the weight ComfyUI would give each, escaped parentheses `\\(` and
    `\\)` being literal text. Requires balanced parentheses; check_user_prompt refuses the others first."""
    return _weights(text.replace("\\(", "").replace("\\)", ""), 1.0)


def check_user_prompt(text: str) -> None:
    """Accepts a positive or negative prompt edited in the gallery's advanced dialog, which keeps ComfyUI's syntax.

    Raises PromptSyntaxRefusedError, with an Italian detail, when the text holds `embedding:` in any case, when an
    unescaped parenthesis is unbalanced (a literal one is written `\\(` or `\\)`), or when any segment would get a
    weight outside 0.5 to 1.5, nesting included: `(((((tag)))))` is 1.1 to the fifth, about 1.61, and is refused. A
    weight that is not a finite number, such as `nan`, is refused too. The text is never changed.
    """
    if "embedding:" in text.lower():
        raise PromptSyntaxRefusedError("Il prompt non può usare «embedding:».")
    depth = 0
    for char in text.replace("\\(", "").replace("\\)", ""):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth < 0:
            break
    if depth != 0:
        raise PromptSyntaxRefusedError(
            "Le parentesi del prompt non sono bilanciate. Una parentesi da mostrare si scrive \\( o \\)."
        )
    for _, weight in prompt_weights(text):
        if not math.isfinite(weight) or not LOWEST_USER_WEIGHT <= weight <= HIGHEST_USER_WEIGHT:
            raise PromptSyntaxRefusedError("Ogni peso del prompt deve essere tra 0,5 e 1,5.")
