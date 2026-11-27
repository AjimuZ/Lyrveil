"""The `card_conversion` job kind (ADR 0047, P10.3).

Revision ID: 0019
Revises: 0018

`generation_jobs.kind` is limited by a CHECK list (revision 0002), so the new kind needs a new list. SQLite cannot alter
a CHECK, and rebuilding the table is unsafe here: `card_imports`, `generated_images`, `image_proposals` and
`scheduled_events` reference it with ON DELETE SET NULL, and dropping the old table with foreign keys on would null
their references. The list only grows, so the schema text is edited in place with the procedure that the SQLite
documentation gives for relaxing a CHECK (`PRAGMA writable_schema`, one transaction, `schema_version` raised).
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"

_BEFORE = "'maintenance')"
_AFTER = "'maintenance', 'card_conversion')"


def _replace_in_schema(old: str, new: str) -> None:
    """Replaces `old` with `new` in the stored CREATE TABLE text of `generation_jobs`, which must hold `old` once."""
    connection = op.get_bind()
    schema = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'generation_jobs'"
    ).scalar_one()
    if schema.count(old) != 1:
        raise RuntimeError("the generation_jobs schema does not have the expected kind list")
    version = int(connection.exec_driver_sql("PRAGMA schema_version").scalar_one())
    connection.exec_driver_sql("PRAGMA writable_schema = ON")
    connection.execute(
        sa.text("UPDATE sqlite_master SET sql = :sql WHERE type = 'table' AND name = 'generation_jobs'"),
        {"sql": schema.replace(old, new)},
    )
    connection.exec_driver_sql(f"PRAGMA schema_version = {version + 1}")
    connection.exec_driver_sql("PRAGMA writable_schema = OFF")


def upgrade() -> None:
    _replace_in_schema(_BEFORE, _AFTER)


def downgrade() -> None:
    # The old list refuses the kind, so its jobs go first; an import that pointed at one loses only that pointer.
    op.execute("DELETE FROM generation_jobs WHERE kind = 'card_conversion'")
    _replace_in_schema(_AFTER, _BEFORE)
