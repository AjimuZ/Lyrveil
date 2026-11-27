"""User persona values and rules that hold without a database (ADR 0048, docs/data-model.md, `user_personas`)."""

from dataclasses import dataclass
from enum import StrEnum

MAX_NAME_CHARS = 40
MIN_AGE = 18
MAX_AGE = 120
MAX_APPEARANCE_CHARS = 600
MAX_ABOUT_CHARS = 1200
# The longest `conversations.persona_note`: what the character knows of the user when the conversation starts.
MAX_NOTE_CHARS = 600
GRAMMATICAL_GENDERS = ("femminile", "maschile", "non_indicato")


class PersonaStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Persona:
    """One `user_personas` row. `age` None means not stated; `appearance` and `about` are never None, only empty.

    `conversation_count` is the number of conversations that use the persona, in any status including deleted ones that
    can still be restored: a persona with a count above 0 can only be archived. Exactly one persona has `is_default`,
    and that one is always active.
    """

    id: str
    name: str
    grammatical_gender: str
    age: int | None
    appearance: str
    about: str
    is_default: bool
    status: PersonaStatus
    conversation_count: int
    created_at: str
    updated_at: str
