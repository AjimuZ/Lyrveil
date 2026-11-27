"""Worlds and lore: `lore_books`, `lore_entries` and `lore_links` (ADR 0049, P12.1).

Revision ID: 0021
Revises: 0020

Follows docs/data-model.md (P12: worlds and lore). The three tables are new, so their foreign keys are real and cascade:
a purge of a character deletes its own book and its links, a book's entries and links follow the book, and a deleted
scenario or conversation takes its links with it. `CHECK`s hold the lengths and ranges, and triggers hold the rules a
`CHECK` cannot see across rows: a book holds at most 500 entries, and only a world can be linked. A character has at
most one own book through the partial unique index `ux_lore_books_character`.

The stemming of keys and the content guard belong to the application (LoreService): SQL cannot call either. The
database therefore only bounds the stored lists, and `keys_stem_json` is any valid JSON.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"

MAX_ENTRIES = 500
_KEY_LISTS = ("keys_json", "secondary_keys_json", "exclude_keys_json")
_TRIGGERS = ("lore_entries_limit", "lore_links_world_only", "lore_links_world_only_update")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _key_list(column: str) -> str:
    """A JSON array of at most 16 items; the CASE keeps json_type from seeing text that is not JSON."""
    return (
        f"CASE WHEN json_valid({column}) THEN json_type({column}) = 'array' AND json_array_length({column}) <= 16 "
        "ELSE 0 END"
    )


def upgrade() -> None:
    op.create_table(
        "lore_books",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("scan_depth", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("token_budget", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("source_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(_one_of("kind", ("character", "world")), name="ck_lore_books_kind"),
        sa.CheckConstraint(
            "(kind = 'character' AND character_id IS NOT NULL) OR (kind = 'world' AND character_id IS NULL)",
            name="ck_lore_books_owner",
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 120", name="ck_lore_books_name"),
        sa.CheckConstraint("length(description) <= 2000", name="ck_lore_books_description"),
        sa.CheckConstraint("scan_depth BETWEEN 1 AND 20", name="ck_lore_books_scan_depth"),
        sa.CheckConstraint(
            "token_budget IS NULL OR token_budget BETWEEN 50 AND 900", name="ck_lore_books_token_budget"
        ),
        sa.CheckConstraint(_one_of("status", ("active", "archived")), name="ck_lore_books_status"),
        sa.CheckConstraint("json_valid(source_json)", name="ck_lore_books_source_json"),
    )
    op.create_index(
        "ux_lore_books_character",
        "lore_books",
        ["character_id"],
        unique=True,
        sqlite_where=sa.text("character_id IS NOT NULL"),
    )
    op.create_index("ix_lore_books_kind_status_name", "lore_books", ["kind", "status", "name"])

    op.create_table(
        "lore_entries",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("book_id", sa.Text(), sa.ForeignKey("lore_books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("secondary_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("exclude_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("keys_stem_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("case_sensitive", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("constant", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("insertion_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("length(title) <= 80", name="ck_lore_entries_title"),
        sa.CheckConstraint("length(content) <= 8000", name="ck_lore_entries_content"),
        *(sa.CheckConstraint(_key_list(column), name=f"ck_lore_entries_{column}") for column in _KEY_LISTS),
        sa.CheckConstraint("json_valid(keys_stem_json)", name="ck_lore_entries_keys_stem_json"),
        sa.CheckConstraint("case_sensitive IN (0, 1)", name="ck_lore_entries_case_sensitive"),
        sa.CheckConstraint("constant IN (0, 1)", name="ck_lore_entries_constant"),
        sa.CheckConstraint("enabled IN (0, 1)", name="ck_lore_entries_enabled"),
        sa.CheckConstraint("length(note) <= 500", name="ck_lore_entries_note"),
        sa.CheckConstraint("json_valid(source_json)", name="ck_lore_entries_source_json"),
    )
    op.create_index("ix_lore_entries_book", "lore_entries", ["book_id", "insertion_order", "id"])
    op.execute(
        "CREATE TRIGGER lore_entries_limit BEFORE INSERT ON lore_entries "
        f"WHEN (SELECT count(*) FROM lore_entries WHERE book_id = NEW.book_id) >= {MAX_ENTRIES} "
        f"BEGIN SELECT RAISE(ABORT, 'a book holds at most {MAX_ENTRIES} entries'); END"
    )

    op.create_table(
        "lore_links",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("book_id", sa.Text(), sa.ForeignKey("lore_books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=True),
        sa.Column("scenario_id", sa.Text(), sa.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=True),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(character_id IS NOT NULL) + (scenario_id IS NOT NULL) + (conversation_id IS NOT NULL) = 1",
            name="ck_lore_links_one_target",
        ),
    )
    # A unique index per target kind: SQLite treats NULLs as distinct, so one composite index would allow repeats.
    for target in ("character_id", "scenario_id", "conversation_id"):
        op.create_index(
            f"ux_lore_links_book_{target}",
            "lore_links",
            ["book_id", target],
            unique=True,
            sqlite_where=sa.text(f"{target} IS NOT NULL"),
        )
        op.create_index(
            f"ix_lore_links_{target}", "lore_links", [target], sqlite_where=sa.text(f"{target} IS NOT NULL")
        )
    for name, event in (("lore_links_world_only", "INSERT"), ("lore_links_world_only_update", "UPDATE OF book_id")):
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {event} ON lore_links "
            "WHEN (SELECT kind FROM lore_books WHERE id = NEW.book_id) IS NOT 'world' "
            "BEGIN SELECT RAISE(ABORT, 'only a world can be linked'); END"
        )


def downgrade() -> None:
    for trigger in _TRIGGERS:
        op.execute(f"DROP TRIGGER {trigger}")
    for target in ("character_id", "scenario_id", "conversation_id"):
        op.drop_index(f"ix_lore_links_{target}", table_name="lore_links")
        op.drop_index(f"ux_lore_links_book_{target}", table_name="lore_links")
    op.drop_table("lore_links")
    op.drop_index("ix_lore_entries_book", table_name="lore_entries")
    op.drop_table("lore_entries")
    op.drop_index("ix_lore_books_kind_status_name", table_name="lore_books")
    op.drop_index("ux_lore_books_character", table_name="lore_books")
    op.drop_table("lore_books")
