"""Lore values and rules that hold without a database: books, worlds, entries, links and stemmed keys (ADR 0049,
docs/data-model.md, P12)."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

MAX_BOOK_NAME_CHARS = 120
MAX_DESCRIPTION_CHARS = 2000
MIN_SCAN_DEPTH = 1
MAX_SCAN_DEPTH = 20
DEFAULT_SCAN_DEPTH = 4
MIN_TOKEN_BUDGET = 50
MAX_TOKEN_BUDGET = 900
# docs/security.md: a book holds at most this many entries. Revision 0021 enforces it with a trigger as well.
MAX_ENTRIES_PER_BOOK = 500

MAX_TITLE_CHARS = 80
MAX_CONTENT_CHARS = 8000
MAX_KEYS_PER_LIST = 16
MAX_KEY_CHARS = 60
MAX_NOTE_CHARS = 500
DEFAULT_PRIORITY = 0
DEFAULT_INSERTION_ORDER = 100
# A bound the documents do not give: it keeps a pasted number inside SQLite's integer range and the editor readable.
MAX_ORDER_MAGNITUDE = 1_000_000

# The maximum tokens of block 11 (constant entries, stable) and block 12 (activated entries, dynamic) in
# docs/memory-engine.md, Context composition. An entry whose rendered line is over its block's maximum never fits.
CONSTANT_BLOCK_MAX_TOKENS = 600
ACTIVATED_BLOCK_MAX_TOKENS = 900
# The language that stems the keys of a world, which has no character: the language of the Lyrveil interface.
WORLD_KEY_LANGUAGE = "it"


class BookKind(StrEnum):
    CHARACTER = "character"
    WORLD = "world"


class BookStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class LoreBook:
    """One `lore_books` row: a character's own book (`kind` character, with `character_id`) or a world (`character_id`
    None). `token_budget` None means only the block's budget applies. `entry_count` is read with the row. `description`
    never reaches a prompt. `source` holds book fields kept from an import."""

    id: str
    kind: BookKind
    character_id: str | None
    name: str
    description: str
    scan_depth: int
    token_budget: int | None
    status: BookStatus
    entry_count: int
    source: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class KeyStems:
    """The stems of an entry's three key lists, written when the entry is saved.

    Each list holds, for every key in the order of the entry's own list, that key's stem sequence: never empty, because
    a key with no stem is refused. `language` is the code the stems were made for, so a matcher that reads another
    language knows to stem the stored key text again. A `case_sensitive` entry stores its stems all the same.
    """

    language: str
    keys: tuple[tuple[str, ...], ...] = ()
    secondary_keys: tuple[tuple[str, ...], ...] = ()
    exclude_keys: tuple[tuple[str, ...], ...] = ()

    def to_json(self) -> str:
        return json.dumps(
            {
                "language": self.language,
                "keys": [list(stems) for stems in self.keys],
                "secondary_keys": [list(stems) for stems in self.secondary_keys],
                "exclude_keys": [list(stems) for stems in self.exclude_keys],
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str) -> "KeyStems":
        """Returns the stems stored by `to_json`; the empty value of a row that holds no stems yet (`{}`) reads as no
        keys in the world language."""
        data = json.loads(text)

        def sequences(name: str) -> tuple[tuple[str, ...], ...]:
            return tuple(tuple(stems) for stems in data.get(name, []))

        return cls(
            language=data.get("language", WORLD_KEY_LANGUAGE),
            keys=sequences("keys"),
            secondary_keys=sequences("secondary_keys"),
            exclude_keys=sequences("exclude_keys"),
        )


def rendered_line(title: str, content: str) -> str:
    """Returns the line an entry takes in block 11 or 12 (docs/prompting.md, Blocks 10 to 12): `- title: content`, or
    `- content` when there is no title. Placeholders are not replaced, so the line is the stored text's own size."""
    return f"- {title}: {content}" if title else f"- {content}"


def exceeds_block_maximum(title: str, content: str, constant: bool, cost: Callable[[str], int]) -> bool:
    """Returns whether the entry's rendered line costs more than the maximum of its block, which is block 11 for a
    constant entry and block 12 otherwise. `cost` returns the tokens of a text; the stored flag is a save-time estimate
    (lyrveil.memory.retrieval.estimate_tokens is deliberately high), and the prompt builder has the runtime's count."""
    limit = CONSTANT_BLOCK_MAX_TOKENS if constant else ACTIVATED_BLOCK_MAX_TOKENS
    return cost(rendered_line(title, content)) > limit


@dataclass(frozen=True)
class LoreEntry:
    """One `lore_entries` row. `keys`, `secondary_keys` and `exclude_keys` keep the text the user typed, and `stems`
    their stem sequences. `never_inserted` is true when the entry's rendered line is over its block's maximum: the entry
    stays stored and editable, but no prompt ever reads it, and the editor says so. `source` holds entry fields and
    decorators kept from an import. `note` never reaches a prompt."""

    id: str
    book_id: str
    title: str
    content: str
    keys: tuple[str, ...]
    secondary_keys: tuple[str, ...]
    exclude_keys: tuple[str, ...]
    stems: KeyStems
    case_sensitive: bool
    constant: bool
    enabled: bool
    priority: int
    insertion_order: int
    note: str
    never_inserted: bool
    source: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class LoreLink:
    """One `lore_links` row: a world linked to exactly one of a character, a scenario or a conversation."""

    id: str
    book_id: str
    character_id: str | None
    scenario_id: str | None
    conversation_id: str | None
    created_at: str
