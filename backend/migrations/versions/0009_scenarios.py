"""Scenarios: reusable starting situations for a conversation.

Revision ID: 0009
Revises: 0008

Follows docs/data-model.md (`scenarios`). `conversations.scenario_id` stays a plain column: SQLite cannot add a foreign
key to an existing table without recreating it, which would drop the full-text triggers of revision 0005. The scenario
service clears the column itself when it deletes a scenario, which is what the documented SET NULL key would do; the
conversation keeps its `scenario_snapshot_json` copy either way.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("setting", sa.Text(), nullable=False, server_default=""),
        sa.Column("opening_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 200", name="ck_scenarios_title"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_scenarios_status"),
    )
    op.create_index("ix_scenarios_status_updated", "scenarios", ["status", sa.text("updated_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_scenarios_status_updated", table_name="scenarios")
    op.drop_table("scenarios")
