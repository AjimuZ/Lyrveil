"""Primary keys and UTC timestamps in the stored forms of docs/data-model.md (Conventions)."""

import os
import time
import uuid
from datetime import UTC, datetime

_VERSION_7 = 0x7 << 76
_RFC_4122_VARIANT = 0x2 << 62


def new_id() -> str:
    """Returns a new UUIDv7 as text: 48 bits of Unix milliseconds, then 74 random bits.

    Identifiers made in a later millisecond sort after earlier ones as text, so primary keys follow creation time.
    Two made within the same millisecond have no defined order. Python 3.12 has no uuid7, hence this helper.
    """
    millis = time.time_ns() // 1_000_000
    random_bits = int.from_bytes(os.urandom(10), "big")
    rand_a = random_bits >> 68  # 12 bits
    rand_b = random_bits & ((1 << 62) - 1)
    value = (millis & ((1 << 48) - 1)) << 80 | _VERSION_7 | rand_a << 64 | _RFC_4122_VARIANT | rand_b
    return str(uuid.UUID(int=value))


def to_utc_text(moment: datetime) -> str:
    """Returns `moment` as UTC ISO-8601 text with milliseconds and a `Z`, such as 2026-11-07T10:00:00.000Z.

    Requires an aware datetime; raises ValueError for a naive one, whose zone would be a guess. Texts of this form
    compare in time order, which SQL ordering and the retry backoff rely on.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("a stored timestamp needs an aware datetime")
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def from_utc_text(text: str) -> datetime:
    """Returns the aware UTC datetime of a text written by to_utc_text. Raises ValueError for any other text."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("a stored timestamp must carry its zone")
    return parsed.astimezone(UTC)
