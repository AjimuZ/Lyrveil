"""Messages and their variants.

Revision ID: 0004
Revises: 0003

`messages` and `message_variants` have the columns of docs/data-model.md, except the keys to tables that do not exist
yet: `scheduled_event_id`, `image_id` and `prompt_assembly_id` are plain columns until their tables are created. The
SET NULL keys from `generation_jobs` to `conversations`, `characters` and `messages` are not added here either; the
job table keeps its plain columns until a revision rebuilds it, and the services keep them consistent meanwhile.
`messages.active_variant_id` and `message_variants.message_id` point at each other, so SQLite enforces only the
second key and the service keeps the first one valid.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("client_message_id", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="text"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("read_state", sa.Text(), nullable=True),
        sa.Column("active_variant_id", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("tokenizer_id", sa.Text(), nullable=True),
        sa.Column("scheduled_event_id", sa.Text(), nullable=True),
        sa.Column("edited_at", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_seq"),
        sa.CheckConstraint(_one_of("author", ("user", "character", "system")), name="ck_messages_author"),
        sa.CheckConstraint(_one_of("kind", ("text", "image", "mixed", "notice")), name="ck_messages_kind"),
        sa.CheckConstraint(
            _one_of("status", ("queued", "streaming", "complete", "failed", "interrupted")), name="ck_messages_status"
        ),
        sa.CheckConstraint(
            "read_state IS NULL OR " + _one_of("read_state", ("sent", "read")), name="ck_messages_read_state"
        ),
        sa.CheckConstraint("seq >= 1", name="ck_messages_seq"),
    )
    op.create_index(
        "ux_messages_client_message_id",
        "messages",
        ["conversation_id", "client_message_id"],
        unique=True,
        sqlite_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_messages_visible",
        "messages",
        ["conversation_id", "seq"],
        sqlite_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "message_variants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("message_id", sa.Text(), sa.ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("variant_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("image_id", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("generation_params_json", sa.Text(), nullable=True),
        sa.Column("checks_json", sa.Text(), nullable=True),
        sa.Column("prompt_assembly_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("message_id", "variant_no", name="uq_message_variants_number"),
        sa.CheckConstraint(_one_of("source", ("user", "generated", "edited")), name="ck_message_variants_source"),
        sa.CheckConstraint(
            _one_of("status", ("streaming", "complete", "failed", "interrupted")), name="ck_message_variants_status"
        ),
        sa.CheckConstraint(
            "generation_params_json IS NULL OR json_valid(generation_params_json)",
            name="ck_message_variants_generation_params_json",
        ),
        sa.CheckConstraint("checks_json IS NULL OR json_valid(checks_json)", name="ck_message_variants_checks_json"),
        sa.CheckConstraint("variant_no >= 1", name="ck_message_variants_number"),
    )


def downgrade() -> None:
    op.drop_table("message_variants")
    op.drop_index("ix_messages_visible", table_name="messages")
    op.drop_index("ux_messages_client_message_id", table_name="messages")
    op.drop_table("messages")
