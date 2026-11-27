"""SQL of `card_imports` and `character_sources` (docs/data-model.md, P10: character cards).

Every function works on a connection the caller owns and never commits.
"""

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.card_imports import SAVED, StoredCardImport, StoredCharacterSource

_IMPORT_COLUMNS = (
    "id, status, format, container, source_json, source_sha256, direct_draft_json, report_json, conversion_job_id, "
    "avatar_staging_path, character_id, created_at, expires_at"
)
_SOURCE_COLUMNS = "id, character_id, format, container, source_json, source_sha256, imported_at"


def _card_import(row: RowMapping) -> StoredCardImport:
    return StoredCardImport(**{name: row[name] for name in StoredCardImport.__dataclass_fields__})


def _source(row: RowMapping) -> StoredCharacterSource:
    return StoredCharacterSource(**{name: row[name] for name in StoredCharacterSource.__dataclass_fields__})


def insert(connection: Connection, stored: StoredCardImport) -> None:
    """Writes the import row. The database refuses a status, format or container outside the documented lists and a
    `source_json` that is not JSON."""
    connection.execute(
        text(
            f"INSERT INTO card_imports ({_IMPORT_COLUMNS}) VALUES (:id, :status, :format, :container, :source_json, "
            ":source_sha256, :direct_draft_json, :report_json, :conversion_job_id, :avatar_staging_path, "
            ":character_id, :created_at, :expires_at)"
        ),
        {name: getattr(stored, name) for name in StoredCardImport.__dataclass_fields__},
    )


def get(connection: Connection, import_id: str) -> StoredCardImport | None:
    """Returns the import, or None when no row has that id."""
    row = (
        connection.execute(text(f"SELECT {_IMPORT_COLUMNS} FROM card_imports WHERE id = :id"), {"id": import_id})
        .mappings()
        .first()
    )
    return None if row is None else _card_import(row)


def set_conversion_job(connection: Connection, import_id: str, job_id: str) -> None:
    """Points the import at its newest `card_conversion` job, replacing the pointer to an earlier one."""
    connection.execute(
        text("UPDATE card_imports SET conversion_job_id = :job WHERE id = :id"), {"job": job_id, "id": import_id}
    )


def delete(connection: Connection, import_id: str) -> None:
    """Removes the import row."""
    connection.execute(text("DELETE FROM card_imports WHERE id = :id"), {"id": import_id})


def delete_expired(connection: Connection, now: str) -> list[str]:
    """Removes every import that is not saved and expired at `now`, and returns their ids, so the caller can remove
    the staged avatars."""
    rows = connection.execute(
        text("SELECT id FROM card_imports WHERE status != :saved AND expires_at <= :now"),
        {"saved": SAVED, "now": now},
    )
    ids = [row[0] for row in rows]
    for import_id in ids:
        delete(connection, import_id)
    return ids


def source_of_character(connection: Connection, character_id: str) -> StoredCharacterSource | None:
    """Returns the stored source card of the character, or None for a character that was not imported."""
    row = (
        connection.execute(
            text(f"SELECT {_SOURCE_COLUMNS} FROM character_sources WHERE character_id = :id"), {"id": character_id}
        )
        .mappings()
        .first()
    )
    return None if row is None else _source(row)
