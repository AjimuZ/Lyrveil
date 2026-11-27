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
# The name and gender revision 0020 gives the default persona when no setting held them.
DEFAULT_NAME = "Utente"
NO_GENDER = "non_indicato"


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


@dataclass(frozen=True)
class ChatPersona:
    """The user as every prompt of one conversation names and describes them (ADR 0048): the `name`,
    `grammatical_gender`, `age`, `appearance` and `about` of the conversation's own persona, and `note`, its
    `persona_note`.

    Block 1, block 3, block 4, the anchor, the stop sequences, the reply checks and the extraction, summary and
    proactive prompts read the name and gender; block 10 reads the rest. It is read again for every reply, so a persona
    or note edit reaches the prompt from the next reply on. The defaults are those of the default persona that revision
    0020 creates when no setting held a name or gender.
    """

    name: str = DEFAULT_NAME
    grammatical_gender: str = NO_GENDER
    age: int | None = None
    appearance: str = ""
    about: str = ""
    note: str = ""
