"""Character versions: the append-only history of the whole card.

Revision ID: 0007
Revises: 0006

Follows docs/data-model.md (`character_versions`) and docs/consistency-engine.md (Versioning). A snapshot holds the card
in the shape of `CharacterCard.model_dump`. Every character that already exists gets its version 1 from the rows it has
now, so `current_version_no` always points at a stored snapshot; the snapshot is built in SQL because a migration must
not depend on application code that will change. Rows are never updated: a trigger refuses it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"

_ACTOR_LIST = ", ".join(repr(actor) for actor in ("user", "import", "restore", "duplicate", "proposal"))

_SNAPSHOT_SQL = """
json_object(
    'identity', json_object(
        'first_name', c.first_name, 'last_name', c.last_name, 'age', c.age,
        'grammatical_gender', c.grammatical_gender, 'narrative_identity', c.narrative_identity,
        'biography', c.biography, 'occupation', c.occupation, 'interests', json(c.interests_json),
        'background', c.background, 'setting', c.setting, 'primary_language', c.primary_language
    ),
    'traits', json((
        SELECT json_group_array(json_object(
            'category', t.category, 'description', t.description,
            'locked', json(CASE WHEN t.locked THEN 'true' ELSE 'false' END)
        ))
        FROM (SELECT * FROM character_traits WHERE character_id = c.id ORDER BY sort_order, id) AS t
    )),
    'communication', json(c.communication_json),
    'visual', json_object(
        'face', v.face, 'hair', v.hair, 'eyes', v.eyes, 'body', v.body, 'clothing', v.clothing,
        'accessories', v.accessories, 'distinctive_marks', v.distinctive_marks, 'art_style', v.art_style,
        'subject_tag', v.subject_tag, 'identity_en', json(v.identity_en_json),
        'usual_outfit_en', json(v.usual_outfit_en_json), 'outfits', json(v.outfits_json),
        'persistent_negative_en', v.persistent_negative_en, 'locked_fields', json(v.locked_fields_json),
        'seed_policy', v.seed_policy, 'base_seed', v.base_seed
    )
)
"""


def upgrade() -> None:
    op.create_table(
        "character_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), sa.ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("character_id", "version_no", name="uq_character_versions_number"),
        sa.CheckConstraint("version_no >= 1", name="ck_character_versions_number"),
        sa.CheckConstraint("json_valid(snapshot_json)", name="ck_character_versions_snapshot_json"),
        sa.CheckConstraint(f"actor IN ({_ACTOR_LIST})", name="ck_character_versions_actor"),
    )
    op.execute(
        "CREATE TRIGGER character_versions_no_update BEFORE UPDATE ON character_versions "
        "BEGIN SELECT RAISE(ABORT, 'character_versions is append-only'); END"
    )
    op.execute(
        "INSERT INTO character_versions "
        "(id, character_id, version_no, snapshot_json, change_summary, actor, created_at) "
        f"SELECT 'v' || c.id || '-' || c.current_version_no, c.id, c.current_version_no, {_SNAPSHOT_SQL}, "
        "'Versione iniziale', 'user', c.created_at "
        "FROM characters AS c JOIN character_visual_profiles AS v ON v.character_id = c.id"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER character_versions_no_update")
    op.drop_table("character_versions")
