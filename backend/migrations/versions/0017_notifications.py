"""Notifications: `notifications` (docs/data-model.md, Scheduling and settings).

Revision ID: 0017
Revises: 0016

The list under the bell icon. `conversation_id` and `message_id` are plain columns with a SET NULL key, so deleting a
chat or a message never blocks and never removes the notice. The first writer is the missed-event step of the scheduler
(P07.3); the service, the API and the `notification.created` event come with P07.5.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"

KINDS = ("proactive_message", "image_ready", "job_failed", "memory_conflict", "missed_events")


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_id", sa.Text(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("read_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(f"kind IN ({', '.join(repr(kind) for kind in KINDS)})", name="ck_notifications_kind"),
    )
    op.create_index("ix_notifications_created", "notifications", ["created_at"])
    op.create_index("ix_notifications_unread", "notifications", ["read_at", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_unread", table_name="notifications")
    op.drop_index("ix_notifications_created", table_name="notifications")
    op.drop_table("notifications")
