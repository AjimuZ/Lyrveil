"""SQL of the `user_personas` table and the persona link of `conversations` and `scenarios`.

Every function works on a connection the caller owns and never commits. Writers open that connection with the WRITE
option of lyrveil.persistence.database, so the service decides what one transaction holds. The database also holds the
rules (revision 0020): one default, never archived, a used persona is never deleted.
"""

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.personas import Persona, PersonaStatus

_SELECT = (
    "SELECT p.id, p.name, p.grammatical_gender, p.age, p.appearance, p.about, p.is_default, p.status, p.created_at, "
    "p.updated_at, (SELECT count(*) FROM conversations AS c WHERE c.persona_id = p.id) AS conversation_count "
    "FROM user_personas AS p"
)


def _persona(row: RowMapping) -> Persona:
    return Persona(
        id=row["id"],
        name=row["name"],
        grammatical_gender=row["grammatical_gender"],
        age=row["age"],
        appearance=row["appearance"],
        about=row["about"],
        is_default=bool(row["is_default"]),
        status=PersonaStatus(row["status"]),
        conversation_count=row["conversation_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert(connection: Connection, persona_id: str, fields: dict[str, object], now: str) -> None:
    """Adds an active, non-default persona. `fields` holds `name`, `grammatical_gender`, `age`, `appearance` and
    `about`."""
    connection.execute(
        text(
            "INSERT INTO user_personas (id, name, grammatical_gender, age, appearance, about, is_default, status, "
            "created_at, updated_at) VALUES (:id, :name, :grammatical_gender, :age, :appearance, :about, 0, 'active', "
            ":now, :now)"
        ),
        {**fields, "id": persona_id, "now": now},
    )


def get(connection: Connection, persona_id: str) -> Persona | None:
    """Returns the persona in any status, or None when no row has that id."""
    row = connection.execute(text(f"{_SELECT} WHERE p.id = :id"), {"id": persona_id}).mappings().first()
    return None if row is None else _persona(row)


def list_all(connection: Connection, status: PersonaStatus) -> list[Persona]:
    """Returns the personas in `status`: the default first, then by name and id."""
    rows = (
        connection.execute(
            text(f"{_SELECT} WHERE p.status = :status ORDER BY p.is_default DESC, p.name COLLATE NOCASE, p.id"),
            {"status": status.value},
        )
        .mappings()
        .all()
    )
    return [_persona(row) for row in rows]


def default_id(connection: Connection) -> str | None:
    """Returns the id of the default persona; None only on a database that revision 0020 has not reached."""
    value = connection.execute(text("SELECT id FROM user_personas WHERE is_default = 1")).scalar_one_or_none()
    return None if value is None else str(value)


def update(connection: Connection, persona_id: str, now: str, **columns: object) -> None:
    """Sets the given columns and `updated_at`. Column names come from the service, never from a request."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    connection.execute(
        text(f"UPDATE user_personas SET {assignments}, updated_at = :now WHERE id = :id"),
        {**columns, "now": now, "id": persona_id},
    )


def make_default(connection: Connection, persona_id: str, now: str) -> None:
    """Moves the default flag to `persona_id`. The old default is cleared first, because the partial unique index
    `ux_user_personas_default` is checked row by row."""
    connection.execute(
        text("UPDATE user_personas SET is_default = 0, updated_at = :now WHERE is_default = 1 AND id <> :id"),
        {"now": now, "id": persona_id},
    )
    connection.execute(
        text("UPDATE user_personas SET is_default = 1, updated_at = :now WHERE id = :id AND is_default = 0"),
        {"now": now, "id": persona_id},
    )


def delete(connection: Connection, persona_id: str) -> None:
    """Removes the persona; the database clears the scenarios that preselect it."""
    connection.execute(text("DELETE FROM user_personas WHERE id = :id"), {"id": persona_id})
