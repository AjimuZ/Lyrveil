"""SQL of the `conversations` table and the character columns a conversation reads.

Every function works on a connection the caller owns and never commits. Writers open that connection with the WRITE
option of lyrveil.persistence.database, so the service decides what one transaction holds.
"""

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.characters import CharacterProfile
from lyrveil.domain.conversations import CharacterRef, Conversation, ConversationStatus

_SELECT = """
SELECT c.id, c.character_id, ch.first_name, ch.last_name, c.title, c.language, ch.primary_language, c.status,
       c.pinned_at, c.shares_memory, c.next_seq, c.last_read_seq, c.last_message_at, c.created_at, c.updated_at,
       c.deleted_at
FROM conversations AS c JOIN characters AS ch ON ch.id = c.character_id
"""
# Pinned chats first (newest pin first), then by the latest activity; a chat with no message counts from its creation.
_ORDER = "ORDER BY c.pinned_at IS NULL, c.pinned_at DESC, COALESCE(c.last_message_at, c.created_at) DESC, c.id DESC"


def _display_name(first_name: str, last_name: str | None) -> str:
    return first_name if not last_name else f"{first_name} {last_name}"


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
        unread_count=max(0, row["next_seq"] - 1 - row["last_read_seq"]),
        last_message_at=row["last_message_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
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
) -> None:
    """Adds an active conversation with `next_seq` 1 and every cursor at 0."""
    connection.execute(
        text(
            "INSERT INTO conversations (id, character_id, title, language, shares_memory, created_at, updated_at) "
            "VALUES (:id, :character_id, :title, :language, :shares_memory, :now, :now)"
        ),
        {
            "id": conversation_id,
            "character_id": character_id,
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


def list_all(connection: Connection, status: ConversationStatus, character_id: str | None) -> list[Conversation]:
    """Returns the conversations in `status`, optionally of one character, in list order: pinned first, then by
    activity."""
    clause = "WHERE c.status = :status" + ("" if character_id is None else " AND c.character_id = :character_id")
    params = {"status": status.value, "character_id": character_id}
    rows = connection.execute(text(f"{_SELECT} {clause} {_ORDER}"), params).mappings().all()
    return [_conversation(row) for row in rows]


def update(connection: Connection, conversation_id: str, now: str, **columns: object) -> None:
    """Sets the given columns of the conversation and its `updated_at`. Column names come from the service, never from
    a request."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    connection.execute(
        text(f"UPDATE conversations SET {assignments}, updated_at = :now WHERE id = :id"),
        {**columns, "now": now, "id": conversation_id},
    )
