"""Prompt assemblies: the diagnostic record of each reply (docs/data-model.md, `prompt_assemblies`).

Revision ID: 0012
Revises: 0011

One row per delivered reply, written by the reply handler after the reply is stored. The conversation key is RESTRICT
like every memory table, so a purge deletes the rows first; the message and variant keys are SET NULL. The
`message_variants.prompt_assembly_id` column stays a plain column: SQLite cannot add a key to an existing column, and
the persistence layer keeps it in step. `prompt_text` is NULL unless diagnostic mode was on when the reply was built.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"


def upgrade() -> None:
    op.create_table(
        "prompt_assemblies",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("message_id", sa.Text(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("variant_id", sa.Text(), sa.ForeignKey("message_variants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("blocks_json", sa.Text(), nullable=False),
        sa.Column("memory_refs_json", sa.Text(), nullable=False),
        sa.Column("summary_ids_json", sa.Text(), nullable=False),
        sa.Column("token_total", sa.Integer(), nullable=False),
        sa.Column("context_limit", sa.Integer(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("json_valid(blocks_json)", name="ck_prompt_assemblies_blocks_json"),
        sa.CheckConstraint("json_valid(memory_refs_json)", name="ck_prompt_assemblies_memory_refs_json"),
        sa.CheckConstraint("json_valid(summary_ids_json)", name="ck_prompt_assemblies_summary_ids_json"),
        sa.CheckConstraint("token_total >= 0 AND context_limit >= 0", name="ck_prompt_assemblies_tokens"),
    )
    op.create_index("ix_prompt_assemblies_conversation", "prompt_assemblies", ["conversation_id", "created_at"])
    op.create_index("ix_prompt_assemblies_message", "prompt_assemblies", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_prompt_assemblies_message", table_name="prompt_assemblies")
    op.drop_index("ix_prompt_assemblies_conversation", table_name="prompt_assemblies")
    op.drop_table("prompt_assemblies")
