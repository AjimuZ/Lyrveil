"""SQL of the `characters` table for the minimal character form of P02.6a.

Every function works on a connection the caller owns and never commits. The full card, its child tables and its
versions arrive with the Creator (P03.1a).
"""

from typing import Any

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.characters import CharacterSummary

_COLUMNS = "id, first_name, last_name, age, grammatical_gender, primary_language, status, created_at, updated_at"


def _summary(row: RowMapping) -> CharacterSummary:
    return CharacterSummary(
        id=row["id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        age=row["age"],
        grammatical_gender=row["grammatical_gender"],
        primary_language=row["primary_language"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert(
    connection: Connection,
    *,
    character_id: str,
    first_name: str,
    last_name: str | None,
    age: int,
    grammatical_gender: str,
    primary_language: str,
    now: str,
) -> None:
    """Inserts an active character at version 1; the free-text card fields keep their empty defaults."""
    connection.execute(
        text(
            "INSERT INTO characters (id, first_name, last_name, age, grammatical_gender, primary_language, "
            "status, created_at, updated_at) "
            "VALUES (:id, :first_name, :last_name, :age, :gender, :language, 'active', :now, :now)"
        ),
        {
            "id": character_id,
            "first_name": first_name,
            "last_name": last_name,
            "age": age,
            "gender": grammatical_gender,
            "language": primary_language,
            "now": now,
        },
    )


def get(connection: Connection, character_id: str) -> CharacterSummary | None:
    """Returns the character, or None when no row has that id."""
    row = (
        connection.execute(text(f"SELECT {_COLUMNS} FROM characters WHERE id = :id"), {"id": character_id})
        .mappings()
        .first()
    )
    return None if row is None else _summary(row)


def list_all(connection: Connection, status: str, like: str | None) -> list[CharacterSummary]:
    """Returns the characters in `status`, newest update first. `like` is a ready LIKE pattern matched against the
    full name, or None for no filter."""
    sql = f"SELECT {_COLUMNS} FROM characters WHERE status = :status"
    params: dict[str, Any] = {"status": status}
    if like is not None:
        sql += " AND lower(first_name || ' ' || COALESCE(last_name, '')) LIKE :like ESCAPE '\\'"
        params["like"] = like
    sql += " ORDER BY updated_at DESC, id DESC"
    return [_summary(row) for row in connection.execute(text(sql), params).mappings()]
