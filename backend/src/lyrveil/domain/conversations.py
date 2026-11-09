"""Conversation values and rules that hold without a database (docs/data-model.md, conversations)."""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

# docs/data-model.md (Deletion policy): a deleted conversation can be restored for this long, then it is purged.
RESTORE_WINDOW = timedelta(days=30)


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(frozen=True)
class CharacterRef:
    """What a conversation needs from its character: the display name, the language it inherits and whether new chats
    may start (an archived character accepts none)."""

    id: str
    name: str
    primary_language: str
    active: bool


class Activity(StrEnum):
    """What the character is doing in a chat right now, for the list row and the header."""

    TYPING = "typing"
    DRAWING = "drawing"
    QUEUED = "queued"


# The preview of the last message is cut to this many characters, so a long reply never bloats the chat list.
PREVIEW_CHARS = 140


@dataclass(frozen=True)
class LastMessage:
    """The newest visible message of a chat that has finished, as the list previews it. `kind` is the message kind
    (`text`, `image` or `mixed`); `text` is its text cut to PREVIEW_CHARS and may be empty for an image."""

    author: str
    kind: str
    text: str


@dataclass(frozen=True)
class Conversation:
    """One conversation row with its character's name. Timestamps are stored UTC text (data-model.md, Conventions).

    `language` is what the row stores, None meaning "inherit"; `effective_language` is the one in force.
    `unread_count` is the finished, visible character messages after `last_read_seq`, so the user's own messages and a
    reply still being produced never count; it is 0 while a chat has none. `last_message` is None until a message has
    finished. `activity` is None while the character does nothing in the chat. `scenario_title` is the title
    in the copy taken when the chat started from a scenario; `scenario_id` is None once that scenario is deleted.
    """

    id: str
    character_id: str
    character_name: str
    title: str
    language: str | None
    effective_language: str
    status: ConversationStatus
    pinned_at: str | None
    shares_memory: bool
    unread_count: int
    last_message_at: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None
    avatar_asset_id: str | None = None
    scenario_id: str | None = None
    scenario_title: str | None = None
    last_message: LastMessage | None = None
    activity: Activity | None = None

    @property
    def pinned(self) -> bool:
        return self.pinned_at is not None
