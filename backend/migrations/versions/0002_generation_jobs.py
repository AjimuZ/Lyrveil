"""The durable job queue: `generation_jobs`.

Revision ID: 0002
Revises: 0001

Columns, checks and the selection index follow docs/data-model.md (generation_jobs). `conversation_id`,
`character_id` and `message_id` are plain columns for now: the tables they reference do not exist yet, and the
revision that creates them adds the SET NULL foreign keys with a batch rebuild of this table.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"

KINDS = (
    "chat_reply",
    "chat_regenerate",
    "character_draft",
    "image_request",
    "visual_profile_translate",
    "proactive_message",
    "image_proposal",
    "summary_checkpoint",
    "memory_extraction",
    "memory_reevaluation",
    "maintenance",
)
STATUSES = ("queued", "running", "completed", "failed", "cancelled")
MODES = ("llm", "image", "none")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("required_mode", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("character_id", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("not_before", sa.Text(), nullable=True),
        sa.Column("lease_id", sa.Text(), nullable=True),
        sa.Column("genkey", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.CheckConstraint(_one_of("kind", KINDS), name="ck_generation_jobs_kind"),
        sa.CheckConstraint(_one_of("status", STATUSES), name="ck_generation_jobs_status"),
        sa.CheckConstraint(_one_of("required_mode", MODES), name="ck_generation_jobs_required_mode"),
        sa.CheckConstraint("json_valid(payload_json)", name="ck_generation_jobs_payload_json"),
        sa.CheckConstraint("result_json IS NULL OR json_valid(result_json)", name="ck_generation_jobs_result_json"),
        sa.CheckConstraint("max_attempts >= 1 AND attempt >= 0", name="ck_generation_jobs_attempts"),
    )
    op.create_index(
        "ix_generation_jobs_selection", "generation_jobs", ["status", "required_mode", "priority", "created_at"]
    )
    op.create_index(
        "ux_generation_jobs_idempotency_key",
        "generation_jobs",
        ["idempotency_key"],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_generation_jobs_idempotency_key", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_selection", table_name="generation_jobs")
    op.drop_table("generation_jobs")
