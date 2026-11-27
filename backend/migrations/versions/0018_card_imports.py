"""Character cards: `card_imports` and `character_sources` (docs/data-model.md, P10: character cards).

Revision ID: 0018
Revises: 0017

`card_imports` holds an import waiting for review: the parsed card unchanged with the file's SHA-256, its format and
container, the staged avatar and the expiry. `direct_draft_json` and `report_json` stay NULL until the direct mapping
(P10.2b) fills them; `conversion_job_id` and `character_id` are set by the conversion (P10.3) and the save (P10.4).
`character_sources` holds the original card of an imported character and follows the character: a purge deletes it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"

STATUSES = ("ready", "converting", "saved", "discarded")
FORMATS = ("chara_card_v1", "chara_card_v2", "chara_card_v3")
CONTAINERS = ("json", "png")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "card_imports",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("container", sa.Text(), nullable=False),
        sa.Column("source_json", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("direct_draft_json", sa.Text(), nullable=True),
        sa.Column("report_json", sa.Text(), nullable=True),
        sa.Column("conversion_job_id", sa.Text(), sa.ForeignKey("generation_jobs.id", ondelete="SET NULL")),
        sa.Column("avatar_staging_path", sa.Text(), nullable=True),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.CheckConstraint(_one_of("status", STATUSES), name="ck_card_imports_status"),
        sa.CheckConstraint(_one_of("format", FORMATS), name="ck_card_imports_format"),
        sa.CheckConstraint(_one_of("container", CONTAINERS), name="ck_card_imports_container"),
        sa.CheckConstraint("json_valid(source_json)", name="ck_card_imports_source_json"),
        sa.CheckConstraint(
            "direct_draft_json IS NULL OR json_valid(direct_draft_json)", name="ck_card_imports_direct_draft_json"
        ),
        sa.CheckConstraint("report_json IS NULL OR json_valid(report_json)", name="ck_card_imports_report_json"),
    )
    op.create_index("ix_card_imports_expires", "card_imports", ["expires_at"])
    op.create_table(
        "character_sources",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("container", sa.Text(), nullable=False),
        sa.Column("source_json", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("imported_at", sa.Text(), nullable=False),
        sa.CheckConstraint(_one_of("format", FORMATS), name="ck_character_sources_format"),
        sa.CheckConstraint(_one_of("container", CONTAINERS), name="ck_character_sources_container"),
        sa.CheckConstraint("json_valid(source_json)", name="ck_character_sources_source_json"),
    )
    op.create_index("ix_character_sources_character", "character_sources", ["character_id"])


def downgrade() -> None:
    op.drop_index("ix_character_sources_character", table_name="character_sources")
    op.drop_table("character_sources")
    op.drop_index("ix_card_imports_expires", table_name="card_imports")
    op.drop_table("card_imports")
