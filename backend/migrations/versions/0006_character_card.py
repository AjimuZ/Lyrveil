"""Child tables of the character card: traits and the visual profile with its identity slots and wardrobe.

Revision ID: 0006
Revises: 0005

Follows docs/data-model.md (Character tables) and ADR 0035. The columns of `characters` already hold identity and the
communication profile, voice fields included, so they do not change. `character_versions` and `character_assets` come
with the revisions that need them, so `primary_avatar_asset_id` is a plain column until the assets table exists. Every
character made by the minimal form of P02.6a gets an empty profile row, so the card reads the same for old and new rows.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


_TRAIT_CATEGORIES = (
    "main_trait",
    "value",
    "motivation",
    "preference",
    "flaw",
    "fear",
    "habit",
    "humor",
    "temperament",
    "relational_style",
)


def upgrade() -> None:
    op.create_table(
        "character_traits",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(_one_of("category", _TRAIT_CATEGORIES), name="ck_character_traits_category"),
        sa.CheckConstraint("length(description) BETWEEN 1 AND 500", name="ck_character_traits_description"),
        sa.CheckConstraint("sort_order >= 0", name="ck_character_traits_sort_order"),
        sa.CheckConstraint("locked IN (0, 1)", name="ck_character_traits_locked"),
    )
    op.create_index("ix_character_traits_order", "character_traits", ["character_id", "category", "sort_order"])

    op.create_table(
        "character_visual_profiles",
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="RESTRICT"), primary_key=True),
        *[
            sa.Column(name, sa.Text(), nullable=False, server_default="")
            for name in ("face", "hair", "eyes", "body", "clothing", "accessories", "distinctive_marks", "art_style")
        ],
        sa.Column("subject_tag", sa.Text(), nullable=False, server_default="1other"),
        sa.Column("identity_en_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("usual_outfit_en_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("outfits_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("persistent_negative_en", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_source_hash", sa.Text(), nullable=True),
        sa.Column("locked_fields_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("seed_policy", sa.Text(), nullable=False, server_default="random"),
        sa.Column("base_seed", sa.Integer(), nullable=True),
        sa.Column("primary_avatar_asset_id", sa.Text(), nullable=True),
        sa.Column("loras_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reference_asset_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(_one_of("subject_tag", ("1girl", "1boy", "1other")), name="ck_character_visual_subject_tag"),
        sa.CheckConstraint(
            _one_of("seed_policy", ("random", "fixed", "base_offset")), name="ck_character_visual_seed_policy"
        ),
        sa.CheckConstraint("base_seed IS NULL OR base_seed >= 0", name="ck_character_visual_base_seed"),
        sa.CheckConstraint(
            "seed_policy = 'random' OR base_seed IS NOT NULL", name="ck_character_visual_seed_policy_needs_seed"
        ),
        sa.CheckConstraint("json_valid(identity_en_json)", name="ck_character_visual_identity_en_json"),
        sa.CheckConstraint("json_valid(usual_outfit_en_json)", name="ck_character_visual_usual_outfit_en_json"),
        sa.CheckConstraint("json_valid(outfits_json)", name="ck_character_visual_outfits_json"),
        sa.CheckConstraint("json_valid(locked_fields_json)", name="ck_character_visual_locked_fields_json"),
        sa.CheckConstraint("json_valid(loras_json)", name="ck_character_visual_loras_json"),
        sa.CheckConstraint("json_valid(reference_asset_ids_json)", name="ck_character_visual_reference_ids_json"),
    )
    op.execute("INSERT INTO character_visual_profiles (character_id, updated_at) SELECT id, updated_at FROM characters")


def downgrade() -> None:
    op.drop_table("character_visual_profiles")
    op.drop_index("ix_character_traits_order", table_name="character_traits")
    op.drop_table("character_traits")
