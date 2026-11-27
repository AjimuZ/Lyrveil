"""Prompt assemblies record the lore entries a reply considered: `prompt_assemblies.lore_refs_json` (ADR 0049, P12.3).

Revision ID: 0022
Revises: 0021

The column holds a JSON array, `[]` for every assembly stored before this revision and for a reply that read no lore.
It is added with raw SQL because the column-level `CHECK` must stay attached to the column: SQLite then lets the
downgrade drop it, which it refuses for a table-level constraint that names the column.
"""

from alembic import op

revision = "0022"
down_revision = "0021"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE prompt_assemblies ADD COLUMN lore_refs_json TEXT NOT NULL DEFAULT '[]' "
        "CHECK (json_valid(lore_refs_json))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE prompt_assemblies DROP COLUMN lore_refs_json")
