"""SQL of the `lore_books`, `lore_entries` and `lore_links` tables (ADR 0049, revision 0021).

Every function works on a connection the caller owns and never commits. Writers open that connection with the WRITE
option of lyrveil.persistence.database, so the service decides what one transaction holds. The database also holds
the rules it can see (revision 0021): lengths and ranges, one own book per character, at most 500 entries in a book,
and links only to worlds.
"""

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.lore import (
    BookKind,
    BookStatus,
    KeyStems,
    LoreBook,
    LoreEntry,
    LoreLink,
    exceeds_block_maximum,
)
from lyrveil.memory.retrieval import estimate_tokens

_BOOK_SELECT = (
    "SELECT b.id, b.kind, b.character_id, b.name, b.description, b.scan_depth, b.token_budget, b.status, "
    "b.source_json, b.created_at, b.updated_at, "
    "(SELECT count(*) FROM lore_entries AS e WHERE e.book_id = b.id) AS entry_count FROM lore_books AS b"
)
_ENTRY_COLUMNS = (
    "id, book_id, title, content, keys_json, secondary_keys_json, exclude_keys_json, keys_stem_json, case_sensitive, "
    "constant, enabled, priority, insertion_order, note, source_json, created_at, updated_at"
)


def _book(row: RowMapping) -> LoreBook:
    return LoreBook(
        id=row["id"],
        kind=BookKind(row["kind"]),
        character_id=row["character_id"],
        name=row["name"],
        description=row["description"],
        scan_depth=row["scan_depth"],
        token_budget=row["token_budget"],
        status=BookStatus(row["status"]),
        entry_count=row["entry_count"],
        source=json.loads(row["source_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _entry(row: RowMapping) -> LoreEntry:
    title, content, constant = row["title"], row["content"], bool(row["constant"])
    return LoreEntry(
        id=row["id"],
        book_id=row["book_id"],
        title=title,
        content=content,
        keys=tuple(json.loads(row["keys_json"])),
        secondary_keys=tuple(json.loads(row["secondary_keys_json"])),
        exclude_keys=tuple(json.loads(row["exclude_keys_json"])),
        stems=KeyStems.from_json(row["keys_stem_json"]),
        case_sensitive=bool(row["case_sensitive"]),
        constant=constant,
        enabled=bool(row["enabled"]),
        priority=row["priority"],
        insertion_order=row["insertion_order"],
        note=row["note"],
        never_inserted=exceeds_block_maximum(title, content, constant, estimate_tokens),
        source=json.loads(row["source_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert_book(
    connection: Connection, book_id: str, kind: BookKind, character_id: str | None, fields: dict[str, Any], now: str
) -> None:
    """Adds a book. `fields` holds `name`, `description`, `scan_depth` and `token_budget`; the database refuses a
    second own book for a character and a world with a character."""
    connection.execute(
        text(
            "INSERT INTO lore_books (id, kind, character_id, name, description, scan_depth, token_budget, status, "
            "created_at, updated_at) VALUES (:id, :kind, :character_id, :name, :description, :scan_depth, "
            ":token_budget, 'active', :now, :now)"
        ),
        {**fields, "id": book_id, "kind": kind.value, "character_id": character_id, "now": now},
    )


def get_book(connection: Connection, book_id: str) -> LoreBook | None:
    """Returns the book in any status, or None when no row has that id."""
    row = connection.execute(text(f"{_BOOK_SELECT} WHERE b.id = :id"), {"id": book_id}).mappings().first()
    return None if row is None else _book(row)


def get_character_book(connection: Connection, character_id: str) -> LoreBook | None:
    """Returns the own book of `character_id`, or None when it has none yet."""
    row = (
        connection.execute(
            text(f"{_BOOK_SELECT} WHERE b.kind = 'character' AND b.character_id = :id"), {"id": character_id}
        )
        .mappings()
        .first()
    )
    return None if row is None else _book(row)


def list_worlds(connection: Connection, status: BookStatus) -> list[LoreBook]:
    """Returns the worlds in `status`, by name and id."""
    rows = (
        connection.execute(
            text(f"{_BOOK_SELECT} WHERE b.kind = 'world' AND b.status = :status ORDER BY b.name COLLATE NOCASE, b.id"),
            {"status": status.value},
        )
        .mappings()
        .all()
    )
    return [_book(row) for row in rows]


def update_book(connection: Connection, book_id: str, now: str, **columns: object) -> None:
    """Sets the given columns and `updated_at`. Column names come from the service, never from a request."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    connection.execute(
        text(f"UPDATE lore_books SET {assignments}, updated_at = :now WHERE id = :id"),
        {**columns, "now": now, "id": book_id},
    )


def delete_book(connection: Connection, book_id: str) -> None:
    """Removes the book; the database deletes its entries and links."""
    connection.execute(text("DELETE FROM lore_books WHERE id = :id"), {"id": book_id})


def character_language(connection: Connection, character_id: str) -> str | None:
    """Returns the primary language of the character, or None when no character has that id."""
    value = connection.execute(
        text("SELECT primary_language FROM characters WHERE id = :id"), {"id": character_id}
    ).scalar_one_or_none()
    return None if value is None else str(value)


def character_first_name(connection: Connection, character_id: str) -> str | None:
    """Returns the first name of the character, or None when no character has that id."""
    value = connection.execute(
        text("SELECT first_name FROM characters WHERE id = :id"), {"id": character_id}
    ).scalar_one_or_none()
    return None if value is None else str(value)


def count_entries(connection: Connection, book_id: str) -> int:
    """Returns the number of entries in the book."""
    count = connection.execute(text("SELECT count(*) FROM lore_entries WHERE book_id = :id"), {"id": book_id})
    return int(count.scalar_one())


def insert_entry(connection: Connection, entry_id: str, book_id: str, columns: dict[str, Any], now: str) -> None:
    """Adds an entry. `columns` holds the editable columns the service validated: `title`, `content`, the three
    `*_keys_json` lists, `keys_stem_json`, the three flags, `priority`, `insertion_order` and `note`."""
    connection.execute(
        text(
            "INSERT INTO lore_entries (id, book_id, title, content, keys_json, secondary_keys_json, exclude_keys_json, "
            "keys_stem_json, case_sensitive, constant, enabled, priority, insertion_order, note, created_at, "
            "updated_at) VALUES (:id, :book_id, :title, :content, :keys_json, :secondary_keys_json, "
            ":exclude_keys_json, :keys_stem_json, :case_sensitive, :constant, :enabled, :priority, :insertion_order, "
            ":note, :now, :now)"
        ),
        {**columns, "id": entry_id, "book_id": book_id, "now": now},
    )


def get_entry(connection: Connection, entry_id: str) -> LoreEntry | None:
    """Returns the entry, or None when no row has that id."""
    row = (
        connection.execute(text(f"SELECT {_ENTRY_COLUMNS} FROM lore_entries WHERE id = :id"), {"id": entry_id})
        .mappings()
        .first()
    )
    return None if row is None else _entry(row)


def list_entries(connection: Connection, book_id: str, enabled: bool | None = None) -> list[LoreEntry]:
    """Returns the entries of a book by `insertion_order` and id, only the enabled or disabled ones when `enabled` is
    given."""
    condition = "" if enabled is None else " AND enabled = :enabled"
    parameters: dict[str, Any] = {"id": book_id} if enabled is None else {"id": book_id, "enabled": enabled}
    rows = (
        connection.execute(
            text(
                f"SELECT {_ENTRY_COLUMNS} FROM lore_entries WHERE book_id = :id{condition} ORDER BY insertion_order, id"
            ),
            parameters,
        )
        .mappings()
        .all()
    )
    return [_entry(row) for row in rows]


def update_entry(connection: Connection, entry_id: str, now: str, columns: dict[str, Any]) -> None:
    """Sets the given editable columns and `updated_at`. Column names come from the service."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    connection.execute(
        text(f"UPDATE lore_entries SET {assignments}, updated_at = :now WHERE id = :id"),
        {**columns, "now": now, "id": entry_id},
    )


def delete_entry(connection: Connection, entry_id: str) -> None:
    connection.execute(text("DELETE FROM lore_entries WHERE id = :id"), {"id": entry_id})


_LINK_COLUMNS = "id, book_id, character_id, scenario_id, conversation_id, created_at"
# The table that holds each link target; names come from this map, never from a request.
_TARGET_TABLES = {"character_id": "characters", "scenario_id": "scenarios", "conversation_id": "conversations"}


def _link(row: RowMapping) -> LoreLink:
    return LoreLink(
        id=row["id"],
        book_id=row["book_id"],
        character_id=row["character_id"],
        scenario_id=row["scenario_id"],
        conversation_id=row["conversation_id"],
        created_at=row["created_at"],
    )


def list_links(connection: Connection, book_id: str) -> list[LoreLink]:
    """Returns the links of a world, oldest first."""
    rows = (
        connection.execute(
            text(f"SELECT {_LINK_COLUMNS} FROM lore_links WHERE book_id = :id ORDER BY created_at, id"),
            {"id": book_id},
        )
        .mappings()
        .all()
    )
    return [_link(row) for row in rows]


def get_link(connection: Connection, link_id: str) -> LoreLink | None:
    """Returns the link, or None when no row has that id."""
    row = (
        connection.execute(text(f"SELECT {_LINK_COLUMNS} FROM lore_links WHERE id = :id"), {"id": link_id})
        .mappings()
        .first()
    )
    return None if row is None else _link(row)


def target_exists(connection: Connection, target: str, target_id: str) -> bool:
    """Returns whether the row `target_id` exists in the table of `target`, which is `character_id`, `scenario_id` or
    `conversation_id`. A conversation in the trash counts as missing, because every route hides it. A character or a
    scenario counts in any status."""
    condition = " AND deleted_at IS NULL" if target == "conversation_id" else ""
    found = connection.execute(
        text(f"SELECT 1 FROM {_TARGET_TABLES[target]} WHERE id = :id{condition}"), {"id": target_id}
    ).first()
    return found is not None


def insert_link(connection: Connection, link_id: str, book_id: str, target: str, target_id: str, now: str) -> None:
    """Adds a link from the world to one target (`character_id`, `scenario_id` or `conversation_id`). The database
    refuses a book that is not a world and a second link of the same pair."""
    connection.execute(
        text(f"INSERT INTO lore_links (id, book_id, {target}, created_at) VALUES (:id, :book_id, :target_id, :now)"),
        {"id": link_id, "book_id": book_id, "target_id": target_id, "now": now},
    )


def delete_link(connection: Connection, link_id: str) -> None:
    connection.execute(text("DELETE FROM lore_links WHERE id = :id"), {"id": link_id})


def copy_scenario_links(
    connection: Connection, scenario_id: str, conversation_id: str, now: str, new_id: Callable[[], str]
) -> int:
    """Gives the conversation its own link to every world the scenario is linked to, with a new id from `new_id` for
    each, and returns how many it added. The copies do not follow the scenario: a later change of the scenario's links
    leaves them as they are (ADR 0049)."""
    rows = connection.execute(
        text("SELECT book_id FROM lore_links WHERE scenario_id = :id ORDER BY created_at, id"), {"id": scenario_id}
    ).all()
    for row in rows:
        insert_link(connection, new_id(), row[0], "conversation_id", conversation_id, now)
    return len(rows)
