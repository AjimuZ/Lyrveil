"""Message values and rules that hold without a database (docs/data-model.md, messages and message_variants)."""

import hashlib
from dataclasses import dataclass
from enum import StrEnum

# docs/api.md (Messages): the longest text a user can send or save in an edit.
MAX_MESSAGE_CHARS = 8000
# docs/api.md (REST conventions, Pagination).
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class Author(StrEnum):
    USER = "user"
    CHARACTER = "character"
    SYSTEM = "system"


class MessageStatus(StrEnum):
    QUEUED = "queued"
    STREAMING = "streaming"
    COMPLETE = "complete"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class VariantSource(StrEnum):
    USER = "user"
    GENERATED = "generated"
    EDITED = "edited"


class VariantStatus(StrEnum):
    STREAMING = "streaming"
    COMPLETE = "complete"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


# A message in one of these states is being produced by a job, so it cannot be edited, regenerated or switched.
PENDING = frozenset({MessageStatus.QUEUED, MessageStatus.STREAMING})


def content_hash(content: str) -> str:
    """Returns the SHA-256 of `content` as lowercase hex, the `content_hash` of a variant."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Variant:
    """One version of a message. `variant_no` counts from 1 within the message and never changes."""

    id: str
    message_id: str
    variant_no: int
    content: str
    source: VariantSource
    status: VariantStatus
    created_at: str


@dataclass(frozen=True)
class Message:
    """One message row. `content` is a copy of the active variant's text; it is empty while the reply is queued.

    `seq` is the message's number in its conversation, from 1 and never reused, so a gap marks a deleted message.
    `variant_count` is how many versions exist. `read_state` is set for user messages only. `image_id` names the image
    of the active variant, None for a message that shows no image; the image's metadata is `GET /images/{id}`.
    """

    id: str
    conversation_id: str
    seq: int
    client_message_id: str | None
    author: Author
    kind: str
    status: MessageStatus
    read_state: str | None
    active_variant_id: str | None
    content: str
    variant_count: int
    edited_at: str | None
    deleted_at: str | None
    created_at: str
    image_id: str | None = None


def message_document(message: Message) -> dict[str, object]:
    """Returns the public fields of `message`, the `message` of the REST answers and of the `message.*` events.

    Leaves out `client_message_id` and `deleted_at`, which only the backend uses.
    """
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "seq": message.seq,
        "author": message.author.value,
        "kind": message.kind,
        "status": message.status.value,
        "read_state": message.read_state,
        "active_variant_id": message.active_variant_id,
        "content": message.content,
        "variant_count": message.variant_count,
        "image_id": message.image_id,
        "edited_at": message.edited_at,
        "created_at": message.created_at,
    }


@dataclass(frozen=True)
class JobRef:
    """The job a send or a regeneration queued. `queue_position` counts the queued and running jobs that run before
    it, itself included, so 1 means it runs next."""

    id: str
    kind: str
    status: str
    queue_position: int


@dataclass(frozen=True)
class SentMessage:
    """What a send returns: the user's message, the reply placeholder and the job that fills it."""

    message: Message
    reply: Message
    job: JobRef


@dataclass(frozen=True)
class Regeneration:
    """What a regeneration returns: the message with its new, active variant and the job that fills it."""

    message: Message
    variant_id: str
    job: JobRef


@dataclass(frozen=True)
class Page:
    """A page of history: `items` ascend by `seq` and are the newest messages older than the request's `before_seq`.

    `next_before_seq` is the `before_seq` that fetches the next older page, or None at the start of the history.
    """

    items: list[Message]
    next_before_seq: int | None
