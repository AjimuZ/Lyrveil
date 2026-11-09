"""SQL of the `conversations` table and the character columns a conversation reads.

Every function works on a connection the caller owns and never commits. Writers open that connection with the WRITE
option of lyrveil.persistence.database, so the service decides what one transaction holds.
"""

import json

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.characters import CharacterProfile
from lyrveil.domain.conversations import (
    PREVIEW_CHARS,
    Activity,
    CharacterRef,
    Conversation,
    ConversationStatus,
    LastMessage,
)

# A message the user can read: not deleted and finished. Queued, streaming and failed replies are not read yet.
_FINISHED = "m.conversation_id = c.id AND m.deleted_at IS NULL AND m.status IN ('complete', 'interrupted')"
_SELECT = f"""
SELECT c.id, c.character_id, ch.first_name, ch.last_name, c.title, c.language, ch.primary_language, c.status,
       c.pinned_at, c.shares_memory, c.next_seq, c.last_read_seq, c.last_message_at, c.created_at, c.updated_at,
       c.deleted_at, c.scenario_id, json_extract(c.scenario_snapshot_json, '$.title') AS scenario_title,
       (SELECT a.id FROM character_assets AS a
        WHERE a.character_id = c.character_id AND a.kind = 'avatar' AND a.is_primary = 1 AND a.deleted_at IS NULL
        LIMIT 1) AS avatar_asset_id,
       (SELECT count(*) FROM messages AS m
        WHERE {_FINISHED} AND m.author = 'character' AND m.seq > c.last_read_seq) AS unread_count,
       (SELECT json_object('author', m.author, 'kind', m.kind, 'text', substr(m.content, 1, {PREVIEW_CHARS + 1}))
        FROM messages AS m WHERE {_FINISHED} ORDER BY m.seq DESC LIMIT 1) AS last_message_json,
       (SELECT json_object('kind', m.kind, 'status', m.status) FROM messages AS m
        WHERE m.conversation_id = c.id AND m.deleted_at IS NULL AND m.author = 'character'
          AND m.status IN ('queued', 'streaming') ORDER BY m.seq DESC LIMIT 1) AS pending_json
FROM conversations AS c JOIN characters AS ch ON ch.id = c.character_id
"""
# Pinned chats first (newest pin first), then by the latest activity; a chat with no message counts from its creation.
_ORDER = "ORDER BY c.pinned_at IS NULL, c.pinned_at DESC, COALESCE(c.last_message_at, c.created_at) DESC, c.id DESC"


def _display_name(first_name: str, last_name: str | None) -> str:
    return first_name if not last_name else f"{first_name} {last_name}"


def _last_message(raw: str | None) -> LastMessage | None:
    if raw is None:
        return None
    fields = json.loads(raw)
    text_value: str = fields["text"]
    if len(text_value) > PREVIEW_CHARS:
        text_value = text_value[:PREVIEW_CHARS].rstrip() + "…"
    return LastMessage(author=fields["author"], kind=fields["kind"], text=text_value)


def _activity(raw: str | None) -> Activity | None:
    """A queued reply is `queued`; one being produced is `drawing` for an image and `typing` otherwise."""
    if raw is None:
        return None
    fields = json.loads(raw)
    if fields["status"] == "queued":
        return Activity.QUEUED
    return Activity.DRAWING if fields["kind"] == "image" else Activity.TYPING


def _conversation(row: RowMapping) -> Conversation:
    return Conversation(
        id=row["id"],
        character_id=row["character_id"],
        character_name=_display_name(row["first_name"], row["last_name"]),
        title=row["title"],
        language=row["language"],
        effective_language=row["language"] or row["primary_language"],
        status=ConversationStatus(row["status"]),
        pinned_at=row["pinned_at"],
        shares_memory=bool(row["shares_memory"]),
        unread_count=row["unread_count"],
        last_message_at=row["last_message_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
        avatar_asset_id=row["avatar_asset_id"],
        scenario_id=row["scenario_id"],
        scenario_title=row["scenario_title"],
        last_message=_last_message(row["last_message_json"]),
        activity=_activity(row["pending_json"]),
    )


def find_character(connection: Connection, character_id: str) -> CharacterRef | None:
    """Returns the character, or None when no row has that id."""
    row = (
        connection.execute(
            text("SELECT id, first_name, last_name, primary_language, status FROM characters WHERE id = :id"),
            {"id": character_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return CharacterRef(
        id=row["id"],
        name=_display_name(row["first_name"], row["last_name"]),
        primary_language=row["primary_language"],
        active=row["status"] == "active",
    )


def find_profile(connection: Connection, character_id: str) -> CharacterProfile | None:
    """Returns the card fields of the reply prompt, or None when no character has that id."""
    row = (
        connection.execute(
            text(
                "SELECT id, first_name, last_name, age, grammatical_gender, narrative_identity, occupation, setting, "
                "biography, primary_language FROM characters WHERE id = :id"
            ),
            {"id": character_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return CharacterProfile(
        id=row["id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        age=row["age"],
        grammatical_gender=row["grammatical_gender"],
        narrative_identity=row["narrative_identity"],
        occupation=row["occupation"],
        setting=row["setting"],
        biography=row["biography"],
        primary_language=row["primary_language"],
    )


def insert(
    connection: Connection,
    *,
    conversation_id: str,
    character_id: str,
    title: str,
    language: str | None,
    shares_memory: bool,
    now: str,
    scenario_id: str | None = None,
    scenario_snapshot: dict[str, object] | None = None,
) -> None:
    """Adds an active conversation with `next_seq` 1 and every cursor at 0. A conversation that starts from a scenario
    stores its id and `scenario_snapshot`, the copy that later edits of the scenario never change."""
    connection.execute(
        text(
            "INSERT INTO conversations (id, character_id, scenario_id, scenario_snapshot_json, title, language, "
            "shares_memory, created_at, updated_at) VALUES (:id, :character_id, :scenario_id, :snapshot, :title, "
            ":language, :shares_memory, :now, :now)"
        ),
        {
            "id": conversation_id,
            "character_id": character_id,
            "scenario_id": scenario_id,
            "snapshot": None if scenario_snapshot is None else json.dumps(scenario_snapshot, ensure_ascii=False),
            "title": title,
            "language": language,
            "shares_memory": int(shares_memory),
            "now": now,
        },
    )


def get(connection: Connection, conversation_id: str) -> Conversation | None:
    """Returns the conversation in any status, or None when no row has that id."""
    row = connection.execute(text(f"{_SELECT} WHERE c.id = :id"), {"id": conversation_id}).mappings().first()
    return None if row is None else _conversation(row)


def list_all(
    connection: Connection, status: ConversationStatus, character_id: str | None, match: str | None = None
) -> list[Conversation]:
    """Returns the conversations in `status`, optionally of one character, in list order: pinned first, then by
    activity. Without a `character_id` the conversations of an archived character are left out, because archiving a
    character hides them with it; asking for one character's conversations lists them whatever its status.

    A `match` (from lyrveil.persistence.search.match_query) keeps the conversations whose title matches or that hold a
    message, not deleted, that matches.
    """
    clause = "WHERE c.status = :status" + (
        " AND ch.status = 'active'" if character_id is None else " AND c.character_id = :character_id"
    )
    if match is not None:
        clause += (
            " AND (c.id IN (SELECT conversation_id FROM conversation_fts WHERE conversation_fts MATCH :match)"
            " OR c.id IN (SELECT m.conversation_id FROM messages AS m WHERE m.deleted_at IS NULL AND m.id IN"
            " (SELECT message_id FROM message_fts WHERE message_fts MATCH :match)))"
        )
    params = {"status": status.value, "character_id": character_id, "match": match}
    rows = connection.execute(text(f"{_SELECT} {clause} {_ORDER}"), params).mappings().all()
    return [_conversation(row) for row in rows]


def mark_read(connection: Connection, conversation_id: str, up_to_seq: int) -> None:
    """Moves `last_read_seq` forward to `up_to_seq`, never past the last message number taken and never back. It does
    not touch `updated_at`: reading is not activity."""
    connection.execute(
        text("UPDATE conversations SET last_read_seq = MAX(last_read_seq, MIN(:up_to, next_seq - 1)) WHERE id = :id"),
        {"up_to": up_to_seq, "id": conversation_id},
    )


def update(connection: Connection, conversation_id: str, now: str, **columns: object) -> None:
    """Sets the given columns of the conversation and its `updated_at`. Column names come from the service, never from
    a request."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    connection.execute(
        text(f"UPDATE conversations SET {assignments}, updated_at = :now WHERE id = :id"),
        {**columns, "now": now, "id": conversation_id},
    )
