"""User personas: `user_personas` and the persona of conversations, scenarios and shared memories (ADR 0048, P11.1).

Revision ID: 0020
Revises: 0019

Follows docs/data-model.md (P11: user personas). The revision creates the default persona from the settings
`user.display_name` and `user.grammatical_gender`, or from their defaults `Utente` and `non_indicato` when a key has no
row or holds a value outside the setting's range. It sets that persona on every conversation and every shared memory,
and deletes the two keys.

The new `persona_id` columns are plain columns, as `conversations.scenario_id` is (revision 0009): SQLite cannot drop a
column that a foreign key names, so a REFERENCES clause would make the downgrade impossible without rebuilding tables
that carry the full-text triggers of revision 0005. Triggers give the documented rules instead: a persona that a
conversation uses cannot be deleted, a conversation's persona never changes once set, and deleting a persona clears the
scenarios that preselect it. The data is moved in SQL and Python only, because a migration must not depend on
application code that will change.
"""

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"

NAME_KEY = "user.display_name"
GENDER_KEY = "user.grammatical_gender"
DEFAULT_NAME = "Utente"
DEFAULT_GENDER = "non_indicato"
GENDERS = ("femminile", "maschile", "non_indicato")
NAME_MAX = 40

_TRIGGERS = (
    "user_personas_in_use",
    "user_personas_clear_scenarios",
    "conversations_persona_fixed",
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _setting(key: str) -> object:
    """Returns the decoded value of setting `key`, or None when it has no row or its text is not JSON."""
    row = (
        op.get_bind()
        .execute(sa.text("SELECT value_json FROM application_settings WHERE key = :key"), {"key": key})
        .first()
    )
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def _default_persona_fields() -> tuple[str, str]:
    """Returns the name and gender of the default persona: the stored settings when they are within the range that
    docs/configuration.md gives them (1 to 40 characters, one of the three genders), and their defaults otherwise, so a
    stored value the table would refuse never stops the upgrade."""
    name = _setting(NAME_KEY)
    if not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX:
        name = DEFAULT_NAME
    gender = _setting(GENDER_KEY)
    if gender not in GENDERS:
        gender = DEFAULT_GENDER
    return name, str(gender)


def upgrade() -> None:
    op.create_table(
        "user_personas",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("grammatical_gender", sa.Text(), nullable=False, server_default=DEFAULT_GENDER),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("appearance", sa.Text(), nullable=False, server_default=""),
        sa.Column("about", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(f"length(name) BETWEEN 1 AND {NAME_MAX}", name="ck_user_personas_name"),
        sa.CheckConstraint(_one_of("grammatical_gender", GENDERS), name="ck_user_personas_grammatical_gender"),
        sa.CheckConstraint("age IS NULL OR age BETWEEN 18 AND 120", name="ck_user_personas_age"),
        sa.CheckConstraint("length(appearance) <= 600", name="ck_user_personas_appearance"),
        sa.CheckConstraint("length(about) <= 1200", name="ck_user_personas_about"),
        sa.CheckConstraint("is_default IN (0, 1)", name="ck_user_personas_is_default"),
        sa.CheckConstraint(_one_of("status", ("active", "archived")), name="ck_user_personas_status"),
        # ADR 0048: the default persona cannot be archived until another one is the default.
        sa.CheckConstraint("NOT (is_default = 1 AND status = 'archived')", name="ck_user_personas_default_active"),
    )
    op.create_index(
        "ux_user_personas_default",
        "user_personas",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )
    op.create_index("ix_user_personas_status_name", "user_personas", ["status", "name"])

    op.execute("ALTER TABLE conversations ADD COLUMN persona_id TEXT")
    op.execute(
        "ALTER TABLE conversations ADD COLUMN persona_note TEXT NOT NULL DEFAULT '' "
        "CONSTRAINT ck_conversations_persona_note CHECK (length(persona_note) <= 600)"
    )
    op.execute("ALTER TABLE scenarios ADD COLUMN persona_id TEXT")
    op.execute(
        "ALTER TABLE memories ADD COLUMN persona_id TEXT "
        "CONSTRAINT ck_memories_persona CHECK (persona_id IS NULL OR scope = 'shared')"
    )
    op.create_index("ix_conversations_persona", "conversations", ["persona_id"])

    name, gender = _default_persona_fields()
    persona_id = str(uuid.uuid4())
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO user_personas (id, name, grammatical_gender, is_default, status, created_at, updated_at) "
            "VALUES (:id, :name, :gender, 1, 'active', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
            "strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
        ),
        {"id": persona_id, "name": name, "gender": gender},
    )
    connection.execute(sa.text("UPDATE conversations SET persona_id = :id"), {"id": persona_id})
    connection.execute(sa.text("UPDATE memories SET persona_id = :id WHERE scope = 'shared'"), {"id": persona_id})
    connection.execute(
        sa.text("DELETE FROM application_settings WHERE key IN (:name, :gender)"),
        {"name": NAME_KEY, "gender": GENDER_KEY},
    )

    op.execute(
        "CREATE TRIGGER user_personas_in_use BEFORE DELETE ON user_personas "
        "WHEN EXISTS (SELECT 1 FROM conversations WHERE persona_id = OLD.id) "
        "BEGIN SELECT RAISE(ABORT, 'a persona that a conversation uses can only be archived'); END"
    )
    op.execute(
        "CREATE TRIGGER user_personas_clear_scenarios AFTER DELETE ON user_personas "
        "BEGIN UPDATE scenarios SET persona_id = NULL WHERE persona_id = OLD.id; END"
    )
    op.execute(
        "CREATE TRIGGER conversations_persona_fixed BEFORE UPDATE OF persona_id ON conversations "
        "WHEN OLD.persona_id IS NOT NULL AND NEW.persona_id IS NOT OLD.persona_id "
        "BEGIN SELECT RAISE(ABORT, 'the persona of a conversation never changes'); END"
    )


def downgrade() -> None:
    # The settings come back from the default persona, so a round trip keeps the user's name and gender; a value equal
    # to its default is left without a row, as before the upgrade on a database where it was never set.
    connection = op.get_bind()
    row = connection.execute(sa.text("SELECT name, grammatical_gender FROM user_personas WHERE is_default = 1")).first()
    if row is not None:
        for key, value, default in ((NAME_KEY, row[0], DEFAULT_NAME), (GENDER_KEY, row[1], DEFAULT_GENDER)):
            if value == default:
                continue
            connection.execute(
                sa.text(
                    "INSERT OR REPLACE INTO application_settings (key, value_json, updated_at) "
                    "VALUES (:key, :value, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
                ),
                {"key": key, "value": json.dumps(value, ensure_ascii=False)},
            )
    for trigger in _TRIGGERS:
        op.execute(f"DROP TRIGGER {trigger}")
    op.drop_index("ix_conversations_persona", table_name="conversations")
    op.execute("ALTER TABLE memories DROP COLUMN persona_id")
    op.execute("ALTER TABLE scenarios DROP COLUMN persona_id")
    op.execute("ALTER TABLE conversations DROP COLUMN persona_note")
    op.execute("ALTER TABLE conversations DROP COLUMN persona_id")
    op.drop_index("ix_user_personas_status_name", table_name="user_personas")
    op.drop_index("ux_user_personas_default", table_name="user_personas")
    op.drop_table("user_personas")
