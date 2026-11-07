"""The orchestrator's events, as the backend stores them: `model_lifecycle_events` (docs/data-model.md).

Revision ID: 0015
Revises: 0014

One row per event of GET /v1/events. `(boot_id, seq)` is unique, so an event read twice, after a backend restart or an
overlapping read, is stored once. `job_id` is plain text without a foreign key, so pruning either table never waits for
the other. The index on `(occurred_at, seq)` serves the read cursor and the retention of the newest rows.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"


def upgrade() -> None:
    op.create_table(
        "model_lifecycle_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("boot_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("vram_total_mib", sa.Integer(), nullable=True),
        sa.Column("vram_used_mib", sa.Integer(), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.CheckConstraint("seq >= 1", name="ck_model_lifecycle_events_seq"),
        sa.CheckConstraint("json_valid(detail_json)", name="ck_model_lifecycle_events_detail_json"),
        sa.UniqueConstraint("boot_id", "seq", name="uq_model_lifecycle_events_boot_seq"),
    )
    op.create_index("ix_model_lifecycle_events_occurred", "model_lifecycle_events", ["occurred_at", "seq"])


def downgrade() -> None:
    op.drop_index("ix_model_lifecycle_events_occurred", table_name="model_lifecycle_events")
    op.drop_table("model_lifecycle_events")
