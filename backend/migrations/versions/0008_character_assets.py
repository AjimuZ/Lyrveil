"""Character assets: uploaded avatars, their crops and the other pictures of a character.

Revision ID: 0008
Revises: 0007

Follows docs/data-model.md (`character_assets`). `thumb_path` is added to the documented columns, because every asset
has a WebP thumbnail and the media routes look paths up in the database, never build them. `generated_image_id` is a
plain column until `generated_images` exists, and `character_visual_profiles.primary_avatar_asset_id` stays plain too:
the service keeps it in step with `is_primary`, which the partial unique index makes the single source of truth.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


_KINDS = ("avatar", "profile", "scene", "variant", "reference")
_SOURCES = ("upload", "generated")
_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")


def upgrade() -> None:
    op.create_table(
        "character_assets",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("generated_image_id", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("thumb_path", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("crop_json", sa.Text(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.Text(), nullable=True),
        sa.CheckConstraint(_one_of("kind", _KINDS), name="ck_character_assets_kind"),
        sa.CheckConstraint(_one_of("source", _SOURCES), name="ck_character_assets_source"),
        sa.CheckConstraint(_one_of("media_type", _MEDIA_TYPES), name="ck_character_assets_media_type"),
        sa.CheckConstraint("width > 0 AND height > 0 AND byte_size > 0", name="ck_character_assets_size"),
        sa.CheckConstraint("crop_json IS NULL OR json_valid(crop_json)", name="ck_character_assets_crop_json"),
        sa.CheckConstraint("is_primary IN (0, 1)", name="ck_character_assets_is_primary"),
        # Only a live avatar can be the primary one.
        sa.CheckConstraint(
            "is_primary = 0 OR (kind = 'avatar' AND deleted_at IS NULL)", name="ck_character_assets_primary_avatar"
        ),
    )
    op.create_index("ix_character_assets_list", "character_assets", ["character_id", "kind", "created_at"])
    op.create_index(
        "uq_character_assets_primary",
        "character_assets",
        ["character_id"],
        unique=True,
        sqlite_where=sa.text("is_primary = 1 AND kind = 'avatar' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_character_assets_primary", table_name="character_assets")
    op.drop_index("ix_character_assets_list", table_name="character_assets")
    op.drop_table("character_assets")
