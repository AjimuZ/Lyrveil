"""Conversation summaries: the versioned checkpoints of docs/memory-engine.md (Progressive summaries).

Revision ID: 0011
Revises: 0010

Follows docs/data-model.md (`conversation_summaries`). A checkpoint is written once: the trigger refuses an UPDATE of
any column but `status`, so a summary is never overwritten. Re-summarizing a range inserts the next `version` and marks
the old row `superseded`. The key to the conversation is RESTRICT, like every memory table.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"

# Every column but `status` and the identifier, which nothing changes after the insert.
_IMMUTABLE = (
    "conversation_id",
    "level",
    "from_seq",
    "to_seq",
    "version",
    "content_json",
    "text",
    "source_hash",
    "prompt_version",
    "token_count",
    "created_at",
)


def upgrade() -> None:
    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("from_seq", sa.Integer(), nullable=False),
        sa.Column("to_seq", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "conversation_id", "level", "from_seq", "to_seq", "version", name="uq_conversation_summaries_range"
        ),
        sa.CheckConstraint("level >= 0", name="ck_conversation_summaries_level"),
        sa.CheckConstraint("from_seq >= 1 AND to_seq >= from_seq", name="ck_conversation_summaries_range"),
        sa.CheckConstraint("version >= 1", name="ck_conversation_summaries_version"),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'invalidated')", name="ck_conversation_summaries_status"
        ),
        sa.CheckConstraint("json_valid(content_json)", name="ck_conversation_summaries_content_json"),
        sa.CheckConstraint("length(text) BETWEEN 1 AND 1600", name="ck_conversation_summaries_text"),
        sa.CheckConstraint("token_count >= 0", name="ck_conversation_summaries_token_count"),
    )
    op.create_index(
        "ix_conversation_summaries_active",
        "conversation_summaries",
        ["conversation_id", "level", "from_seq"],
        sqlite_where=sa.text("status = 'active'"),
    )
    op.execute(
        f"CREATE TRIGGER conversation_summaries_immutable BEFORE UPDATE OF {', '.join(_IMMUTABLE)} "
        "ON conversation_summaries BEGIN SELECT RAISE(ABORT, 'a summary checkpoint is never overwritten'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER conversation_summaries_immutable")
    op.drop_index("ix_conversation_summaries_active", table_name="conversation_summaries")
    op.drop_table("conversation_summaries")
