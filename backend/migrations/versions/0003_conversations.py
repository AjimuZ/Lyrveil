"""Conversations and the minimal `characters` table they refer to.

Revision ID: 0003
Revises: 0002

`characters` has the columns of docs/data-model.md without its child tables; the character card of P03.1a adds those.
`conversations.scenario_id` is a plain column until the revision that creates `scenarios` adds its SET NULL key.
The job table keeps its plain `conversation_id`, `character_id` and `message_id` columns until `messages` exists.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "characters",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("grammatical_gender", sa.Text(), nullable=False, server_default="non_indicato"),
        sa.Column("narrative_identity", sa.Text(), nullable=False, server_default=""),
        sa.Column("biography", sa.Text(), nullable=False, server_default=""),
        sa.Column("background", sa.Text(), nullable=False, server_default=""),
        sa.Column("setting", sa.Text(), nullable=False, server_default=""),
        sa.Column("occupation", sa.Text(), nullable=False, server_default=""),
        sa.Column("interests_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("primary_language", sa.Text(), nullable=False, server_default="it"),
        sa.Column("communication_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("current_version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("archived_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("length(first_name) BETWEEN 1 AND 80", name="ck_characters_first_name"),
        sa.CheckConstraint("age >= 18", name="ck_characters_age"),
        sa.CheckConstraint(
            _one_of("grammatical_gender", ("femminile", "maschile", "non_indicato")),
            name="ck_characters_grammatical_gender",
        ),
        sa.CheckConstraint("json_valid(interests_json)", name="ck_characters_interests_json"),
        sa.CheckConstraint("json_valid(communication_json)", name="ck_characters_communication_json"),
        sa.CheckConstraint(_one_of("status", ("active", "archived")), name="ck_characters_status"),
        sa.CheckConstraint("is_demo IN (0, 1)", name="ck_characters_is_demo"),
    )
    op.create_index("ix_characters_status_updated", "characters", ["status", sa.text("updated_at DESC")])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scenario_id", sa.Text(), nullable=True),
        sa.Column("scenario_snapshot_json", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("pinned_at", sa.Text(), nullable=True),
        sa.Column("shares_memory", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("next_seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_message_at", sa.Text(), nullable=True),
        sa.Column("last_read_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_extracted_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_summarized_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proactive_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.Text(), nullable=True),
        sa.CheckConstraint(_one_of("status", ("active", "archived", "deleted")), name="ck_conversations_status"),
        sa.CheckConstraint("shares_memory IN (0, 1)", name="ck_conversations_shares_memory"),
        sa.CheckConstraint(
            "scenario_snapshot_json IS NULL OR json_valid(scenario_snapshot_json)",
            name="ck_conversations_scenario_snapshot_json",
        ),
        sa.CheckConstraint(
            "proactive_json IS NULL OR json_valid(proactive_json)", name="ck_conversations_proactive_json"
        ),
        sa.CheckConstraint("next_seq >= 1", name="ck_conversations_next_seq"),
    )
    op.create_index(
        "ix_conversations_list",
        "conversations",
        ["status", "pinned_at", sa.text("last_message_at DESC")],
    )
    op.create_index("ix_conversations_character_status", "conversations", ["character_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_conversations_character_status", table_name="conversations")
    op.drop_index("ix_conversations_list", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_characters_status_updated", table_name="characters")
    op.drop_table("characters")
