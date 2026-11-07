"""Generated images: the stored result of each `image_request` job (docs/data-model.md, `generated_images`).

Revision ID: 0013
Revises: 0012

`job_id` is unique, so persisting the same job twice cannot add a second image. The message and conversation keys are
SET NULL, because the image stays in the gallery when its chat goes; the character key is RESTRICT like every other
table that a purge must delete first. `message_variants.image_id` and `character_assets.generated_image_id` stay plain
columns: SQLite cannot add a key to an existing column, and the persistence layer keeps them in step.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


_PURPOSES = ("chat", "avatar", "profile", "scene", "variant")
_ASPECTS = ("square", "portrait", "landscape", "tall", "wide")
_PRESETS = ("alta", "rapida")


def upgrade() -> None:
    op.create_table(
        "generated_images",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_id", sa.Text(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("thumb_path", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("scene_json", sa.Text(), nullable=False),
        sa.Column("aspect", sa.Text(), nullable=False),
        sa.Column("outfit_key", sa.Text(), nullable=False),
        sa.Column("prompt_positive", sa.Text(), nullable=False),
        sa.Column("prompt_negative", sa.Text(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("cfg", sa.Float(), nullable=False),
        sa.Column("sampler", sa.Text(), nullable=False),
        sa.Column("scheduler", sa.Text(), nullable=False),
        sa.Column("clip_skip", sa.Integer(), nullable=False),
        sa.Column("checkpoint_file", sa.Text(), nullable=False),
        sa.Column("checkpoint_sha256", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("profile_id", sa.Text(), nullable=False),
        sa.Column("quality_preset", sa.Text(), nullable=False),
        sa.Column("hires_denoise", sa.Float(), nullable=True),
        sa.Column("hires_scale", sa.Float(), nullable=True),
        sa.Column("hires_steps", sa.Integer(), nullable=True),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("workflow_json", sa.Text(), nullable=False),
        sa.Column("comfyui_version", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.Text(), nullable=True),
        sa.CheckConstraint(_one_of("purpose", _PURPOSES), name="ck_generated_images_purpose"),
        sa.CheckConstraint(_one_of("aspect", _ASPECTS), name="ck_generated_images_aspect"),
        sa.CheckConstraint(_one_of("quality_preset", _PRESETS), name="ck_generated_images_quality_preset"),
        sa.CheckConstraint("width > 0 AND height > 0 AND byte_size > 0", name="ck_generated_images_size"),
        sa.CheckConstraint("json_valid(scene_json)", name="ck_generated_images_scene_json"),
        sa.CheckConstraint("json_valid(workflow_json)", name="ck_generated_images_workflow_json"),
    )
    op.create_index("uq_generated_images_job", "generated_images", ["job_id"], unique=True)
    op.create_index("ix_generated_images_character", "generated_images", ["character_id", "created_at"])
    op.create_index("ix_generated_images_conversation", "generated_images", ["conversation_id", "created_at"])
    op.create_index("ix_generated_images_message", "generated_images", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_generated_images_message", table_name="generated_images")
    op.drop_index("ix_generated_images_conversation", table_name="generated_images")
    op.drop_index("ix_generated_images_character", table_name="generated_images")
    op.drop_index("uq_generated_images_job", table_name="generated_images")
    op.drop_table("generated_images")
