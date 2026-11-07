"""SQL of the `scenarios` table and the `scenario_id` link of `conversations`.

Every function works on a connection the caller owns and never commits. Writers open that connection with the WRITE
option of lyrveil.persistence.database, so the service decides what one transaction holds.
"""

import json

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.scenarios import Scenario, ScenarioStatus

_SELECT = (
    "SELECT id, title, description, setting, opening_message, character_id, language, status, created_at, updated_at "
    "FROM scenarios"
)
_COLUMNS = ("title", "description", "setting", "opening_message", "character_id", "language")


def _scenario(row: RowMapping) -> Scenario:
    return Scenario(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        setting=row["setting"],
        opening_message=row["opening_message"],
        character_id=row["character_id"],
        language=row["language"],
        status=ScenarioStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def character_exists(connection: Connection, character_id: str) -> bool:
    """Returns whether a character row has that id, in any status."""
    found = connection.execute(text("SELECT 1 FROM characters WHERE id = :id"), {"id": character_id}).first()
    return found is not None


def insert(connection: Connection, scenario_id: str, fields: dict[str, object], now: str) -> None:
    """Adds an active scenario. `fields` holds exactly the keys of _COLUMNS."""
    connection.execute(
        text(
            "INSERT INTO scenarios (id, title, description, setting, opening_message, character_id, language, "
            "created_at, updated_at) VALUES (:id, :title, :description, :setting, :opening_message, :character_id, "
            ":language, :now, :now)"
        ),
        {**fields, "id": scenario_id, "now": now},
    )


def get(connection: Connection, scenario_id: str) -> Scenario | None:
    """Returns the scenario in any status, or None when no row has that id."""
    row = connection.execute(text(f"{_SELECT} WHERE id = :id"), {"id": scenario_id}).mappings().first()
    return None if row is None else _scenario(row)


def list_all(connection: Connection, status: ScenarioStatus, character_id: str | None) -> list[Scenario]:
    """Returns the scenarios in `status`, newest change first. With a `character_id` it keeps those usable with that
    character: its own and the ones for any character."""
    clause = "WHERE status = :status"
    params: dict[str, object] = {"status": status.value}
    if character_id is not None:
        clause += " AND (character_id IS NULL OR character_id = :character_id)"
        params["character_id"] = character_id
    rows = connection.execute(text(f"{_SELECT} {clause} ORDER BY updated_at DESC, id DESC"), params).mappings().all()
    return [_scenario(row) for row in rows]


def update(connection: Connection, scenario_id: str, now: str, **columns: object) -> None:
    """Sets the given columns (_COLUMNS or `status`) and `updated_at`. Column names come from the service, never from a
    request."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    connection.execute(
        text(f"UPDATE scenarios SET {assignments}, updated_at = :now WHERE id = :id"),
        {**columns, "now": now, "id": scenario_id},
    )


def delete(connection: Connection, scenario_id: str) -> None:
    """Removes the scenario and clears `scenario_id` of the conversations that started from it, which keep their
    snapshot. `conversations.scenario_id` has no foreign key (migration 0009), so this is the SET NULL."""
    connection.execute(text("UPDATE conversations SET scenario_id = NULL WHERE scenario_id = :id"), {"id": scenario_id})
    connection.execute(text("DELETE FROM scenarios WHERE id = :id"), {"id": scenario_id})


def snapshot_of(connection: Connection, conversation_id: str) -> dict[str, object] | None:
    """Returns the scenario copy a conversation was started with, or None when it had none."""
    raw = connection.execute(
        text("SELECT scenario_snapshot_json FROM conversations WHERE id = :id"), {"id": conversation_id}
    ).scalar_one_or_none()
    if raw is None:
        return None
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else None
