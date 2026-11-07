"""Image proposals: the contextual suggestions a user answers (docs/data-model.md, `image_proposals`).

Revision ID: 0014
Revises: 0013

A proposal belongs to its conversation and to the message it followed. Both keys are RESTRICT like every other table
that a purge must delete first (docs/data-model.md, Deletion policy): `characters.purge` removes the proposals before
the messages. `job_id` is the `image_request` job an acceptance queued and becomes NULL if the queue prunes the job.
`decided_at` is set exactly when the status leaves `pending`.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"


def upgrade() -> None:
    op.create_table(
        "image_proposals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("message_id", sa.Text(), sa.ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scene_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'expired')", name="ck_image_proposals_status"
        ),
        sa.CheckConstraint("json_valid(scene_json)", name="ck_image_proposals_scene_json"),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)", name="ck_image_proposals_decided_with_status"
        ),
    )
    op.create_index("ix_image_proposals_conversation", "image_proposals", ["conversation_id", "created_at"])
    op.create_index("ix_image_proposals_status", "image_proposals", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_image_proposals_status", table_name="image_proposals")
    op.drop_index("ix_image_proposals_conversation", table_name="image_proposals")
    op.drop_table("image_proposals")
