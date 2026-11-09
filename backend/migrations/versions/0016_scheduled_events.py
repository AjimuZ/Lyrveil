"""Scheduled events: `scheduled_events` (docs/data-model.md, Scheduling and settings).

Revision ID: 0016
Revises: 0015

One row per proactive trigger of docs/scheduler.md. `job_id` keeps its SET NULL key, so pruning old jobs never blocks.
`message_id` is a plain column and `messages.scheduled_event_id` stays a plain column too, like `image_id`: SQLite can
add a key only by rebuilding `messages`, which its search triggers and every reference to it make a risk for no gain,
and the generation stage (P07.2) writes both columns in one transaction. `notifications` joins with the notification
step (P07.5).
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "scheduled_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("due_at", sa.Text(), nullable=False),
        sa.Column("recurrence_json", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("missed_policy", sa.Text(), nullable=False, server_default="skip"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("outcome_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            _one_of("kind", ("scheduled_message", "inactivity_check", "follow_up", "contextual_initiative")),
            name="ck_scheduled_events_kind",
        ),
        sa.CheckConstraint(
            _one_of("status", ("pending", "running", "delivered", "skipped", "missed", "cancelled", "failed")),
            name="ck_scheduled_events_status",
        ),
        sa.CheckConstraint(
            _one_of("missed_policy", ("skip", "deliver_once", "next_window")), name="ck_scheduled_events_missed_policy"
        ),
        sa.CheckConstraint(_one_of("created_by", ("user", "scheduler")), name="ck_scheduled_events_created_by"),
        sa.CheckConstraint(
            "recurrence_json IS NULL OR json_valid(recurrence_json)", name="ck_scheduled_events_recurrence_json"
        ),
    )
    op.create_index("ix_scheduled_events_status_due", "scheduled_events", ["status", "due_at"])
    op.create_index("ix_scheduled_events_conversation", "scheduled_events", ["conversation_id", "kind", "status"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_events_conversation", table_name="scheduled_events")
    op.drop_index("ix_scheduled_events_status_due", table_name="scheduled_events")
    op.drop_table("scheduled_events")
