"""Baseline: the runtime settings table.

Revision ID: 0001
Revises: none

The domain tables arrive with the phases that need them (docs/data-model.md), each in its own revision. The
baseline holds only `application_settings`, so a fresh database is already versioned and the migration path is proven.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "application_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("json_valid(value_json)", name="ck_application_settings_value_json"),
    )


def downgrade() -> None:
    op.drop_table("application_settings")
