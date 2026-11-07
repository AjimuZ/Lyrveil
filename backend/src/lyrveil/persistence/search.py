"""Turns user search text into a safe FTS5 query and finds the messages that match it."""

import re

from sqlalchemy import Connection

from lyrveil.domain.messages import Message
from lyrveil.persistence.messages import message_rows

_WORD = re.compile(r"\w+")


def match_query(user_text: str) -> str | None:
    """Returns an FTS5 MATCH expression that finds rows containing every word of `user_text`, or None when it has no
    word.

    Only the letters and digits of the text reach the expression, and each word is a double-quoted string, so
    operators (AND, OR, NOT, NEAR), quotes, parentheses, `*`, `^`, `:` and `-` can neither raise a syntax error nor
    change the meaning of the query. Accent folding is the index tokenizer's job, not this function's.
    """
    words = _WORD.findall(user_text)
    if not words:
        return None
    return " ".join(f'"{word}"' for word in words)


def search_messages(connection: Connection, conversation_id: str, match: str, limit: int) -> list[Message]:
    """Returns up to `limit` messages of the conversation that are not deleted and match `match`, newest first.

    `match` must come from `match_query`.
    """
    return message_rows(
        connection,
        "WHERE m.conversation_id = :id AND m.deleted_at IS NULL AND m.id IN "
        "(SELECT message_id FROM message_fts WHERE message_fts MATCH :match) ORDER BY m.seq DESC LIMIT :limit",
        {"id": conversation_id, "match": match, "limit": limit},
    )
