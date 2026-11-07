"""Extraction windows and triggers (docs/memory-engine.md, Triggers and Window): which messages one run reads, the hash
that makes a run idempotent, and when a conversation is due for one. Pure: the callers read and write the database."""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from lyrveil.domain.messages import PENDING, Author, MessageStatus
from lyrveil.memory.text import language_code, normalize

# docs/memory-engine.md (Triggers): `memory.extract_every_messages`, its default and its allowed range.
DEFAULT_EVERY_MESSAGES = 10
MIN_EVERY_MESSAGES = 8
MAX_EVERY_MESSAGES = 12
# docs/memory-engine.md (Window): the most messages one run analyzes, and the earlier ones it shows as context.
MAX_WINDOW_MESSAGES = 12
CONTEXT_MESSAGES = 2
# A significant event triggers a run only with at least this many unprocessed messages.
SIGNIFICANT_MIN_MESSAGES = 2
# Pattern lists by language code, one phrase per line (docs/memory-engine.md, Triggers).
PATTERN_DIR = Path(__file__).resolve().parents[1] / "prompts"
PATTERN_FILE = "significant_patterns.txt"

_FINISHED = frozenset({MessageStatus.COMPLETE, MessageStatus.INTERRUPTED})
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class ExtractionSettings:
    """The extraction settings the backend has before the settings store exists: `every_messages` is
    `memory.extract_every_messages`. Raises ValueError outside MIN_EVERY_MESSAGES to MAX_EVERY_MESSAGES."""

    every_messages: int = DEFAULT_EVERY_MESSAGES

    def __post_init__(self) -> None:
        if not MIN_EVERY_MESSAGES <= self.every_messages <= MAX_EVERY_MESSAGES:
            raise ValueError(f"every_messages must be between {MIN_EVERY_MESSAGES} and {MAX_EVERY_MESSAGES}")


@dataclass(frozen=True)
class WindowMessage:
    """A message after the cursor, with its active variant: `content` and `content_hash` are that variant's.

    `variant_id` and `content_hash` are None only for a message without an active variant, which is never readable.
    """

    message_id: str
    seq: int
    author: Author
    status: MessageStatus
    deleted: bool
    content: str
    variant_id: str | None
    content_hash: str | None
    created_at: str

    @property
    def pending(self) -> bool:
        """A reply still being produced: the window stops before it, so the cursor never passes an unfinished one."""
        return not self.deleted and self.status in PENDING

    @property
    def readable(self) -> bool:
        """A message the model may analyze: not deleted, by the user or the character, finished (complete or
        interrupted), with text and an active variant. Other settled messages are passed over."""
        return (
            not self.deleted
            and self.author is not Author.SYSTEM
            and self.status in _FINISHED
            and bool(self.content.strip())
            and self.variant_id is not None
            and self.content_hash is not None
        )


@dataclass(frozen=True)
class Window:
    """What one run analyzes: the readable messages from `from_seq` to `to_seq`, oldest first, and their hash.

    Always holds: `from_seq` is the cursor plus one, `to_seq` is the seq of the last message, 1 to MAX_WINDOW_MESSAGES
    messages, and `messages` hold only readable messages. Passing the window moves the cursor to `to_seq`.
    """

    from_seq: int
    to_seq: int
    messages: tuple[WindowMessage, ...]
    hash: str


def readable_before_pending(after_cursor: Sequence[WindowMessage]) -> list[WindowMessage]:
    """Returns the readable messages of `after_cursor` (ordered by seq) that come before the first pending one."""
    readable: list[WindowMessage] = []
    for message in after_cursor:
        if message.pending:
            break
        if message.readable:
            readable.append(message)
    return readable


def window_hash(messages: Sequence[WindowMessage]) -> str:
    """Returns the SHA-256, as lowercase hex, of the message id, active variant id and content hash of each message, in
    order. An edit, a regeneration, a variant switch or a deletion inside the window changes it."""
    digest = hashlib.sha256()
    for message in messages:
        digest.update(f"{message.message_id}\x1f{message.variant_id}\x1f{message.content_hash}\x1e".encode())
    return digest.hexdigest()


def select_window(
    after_cursor: Sequence[WindowMessage], cursor: int, max_messages: int = MAX_WINDOW_MESSAGES
) -> Window | None:
    """Returns the next window after `cursor`, or None when no readable message waits before the first pending one.

    `after_cursor` are the messages with a seq above `cursor`, ordered by seq. The window takes the first
    `max_messages` readable ones; deleted, failed, empty and system messages are passed over, and a pending reply ends
    the window, so the cursor never passes it.
    """
    picked = readable_before_pending(after_cursor)[:max_messages]
    if not picked:
        return None
    return Window(from_seq=cursor + 1, to_seq=picked[-1].seq, messages=tuple(picked), hash=window_hash(picked))


@cache
def significant_patterns(language: str | None) -> tuple[re.Pattern[str], ...]:
    """Returns the significant-event patterns of a language, compiled; empty when the language has no list.

    The list is `prompts/<code>/significant_patterns.txt`: one phrase per line, blank lines and `#` comments ignored. A
    phrase matches at the start of a word of the normalized text and may end inside one, so `mi sono trasferit`
    matches "mi sono trasferita".
    """
    code = language_code(language)
    path = PATTERN_DIR / (code or "") / PATTERN_FILE
    if code is None or not path.is_file():
        return ()
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    phrases = (_SPACES.sub(" ", normalize(line)) for line in lines if line)
    return tuple(re.compile(r"(?<![^\W_])" + re.escape(phrase)) for phrase in phrases if phrase)


def is_significant(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    """Tells whether `text`, normalized like the patterns (case, accents, runs of spaces), contains one of them."""
    prepared = _SPACES.sub(" ", normalize(text))
    return any(pattern.search(prepared) for pattern in patterns)


def is_due(after_cursor: Sequence[WindowMessage], every_messages: int, patterns: Sequence[re.Pattern[str]]) -> bool:
    """Tells whether a conversation needs an extraction run, from its messages after the cursor ordered by seq.

    Counts the readable messages before the first pending one, the messages a window would take. Due with at least
    `every_messages` of them, or with at least SIGNIFICANT_MIN_MESSAGES when one of the user's matches a significant
    pattern (docs/memory-engine.md, Triggers).
    """
    readable = readable_before_pending(after_cursor)
    if len(readable) >= every_messages:
        return True
    return len(readable) >= SIGNIFICANT_MIN_MESSAGES and any(
        message.author is Author.USER and is_significant(message.content, patterns) for message in readable
    )
