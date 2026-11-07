"""Prompt pieces shared by every prompt of a chat: the user as prompts name them, the language names and the scenario
block.

Since P04.5 lyrveil.context.builder composes the reply prompt and lyrveil.context.blocks renders its blocks; the
structured tasks (extraction, summaries) read the names below too.
"""

from collections.abc import Mapping
from dataclasses import dataclass

# docs/prompting.md (Language resolution): the supported codes, by the name block 1 writes.
LANGUAGE_NAMES = {"it": "italiano", "en": "inglese", "fr": "francese", "de": "tedesco", "es": "spagnolo"}
NO_GENDER = "non_indicato"


@dataclass(frozen=True)
class UserProfile:
    """The user as the prompt names them: the settings `user.display_name` and `user.grammatical_gender`."""

    display_name: str = "Utente"
    grammatical_gender: str = NO_GENDER


def scenario_block(snapshot: Mapping[str, object] | None) -> str:
    """Returns the `Scenario:` block of the stable text for the copy a conversation stored at its start, or "" when it
    started without a scenario or the copy has no description and no setting. Only that one conversation's copy is
    read, so a scenario edited or deleted later, or another chat of the same character, never changes it."""
    if not snapshot:
        return ""
    description = str(snapshot.get("description") or "").strip()
    setting = str(snapshot.get("setting") or "").strip()
    if not description and not setting:
        return ""
    lines = [f"Scenario: {snapshot.get('title') or ''}".strip(), description]
    if setting:
        lines.append(f"Ambientazione: {setting}")
    return "\n".join(line for line in lines if line)
