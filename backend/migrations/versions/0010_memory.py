"""Memory: shared and conversation memories, episodes, relationship state and their full-text indexes.

Revision ID: 0010
Revises: 0009

Follows docs/data-model.md (Memory tables) for layers B to E of docs/memory-engine.md. `conversation_summaries` and
`prompt_assemblies` belong to the summary and context-builder tasks and are not created here.

Every foreign key is RESTRICT, except SET NULL where the document says so and CASCADE on `memory_revisions` and
`memory_sources`, which mean nothing without their memory. `memory_revisions` refuses UPDATE, like `character_versions`.
`memory_fts` and `episode_fts` have no triggers: the text is stemmed in Python (`lyrveil.memory.text`), so the
application writes the index row in the same transaction as the source row (`lyrveil.persistence.memory_index`).

`messages.prompt_assembly_id` and the state columns that point at tables of later tasks stay plain columns.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"

_TOKENIZER = 'tokenize = "unicode61 remove_diacritics 2"'


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


_MEMORY_KINDS = (
    "user_fact",
    "user_preference",
    "character_fact",
    "narrative_event",
    "promise",
    "decision",
    "open_thread",
    "boundary",
    "identity_candidate",
)
_PROVENANCES = ("stated", "inferred", "fictional", "user_confirmed")
_STATUSES = ("active", "uncertain", "superseded", "contradicted", "expired", "invalidated")
_EVENT_KINDS = (
    "relationship_change",
    "emotion_change",
    "thread_opened",
    "thread_closed",
    "goal_change",
    "narrative_change",
    "user_correction",
)
_DIMENSIONS = ("familiarity", "trust", "confidence", "tension")


def _fk(target: str, ondelete: str) -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete=ondelete)


def _create_runs() -> None:
    op.create_table(
        "memory_extraction_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), _fk("conversations.id", "RESTRICT"), nullable=False),
        sa.Column("from_seq", sa.Integer(), nullable=False),
        sa.Column("to_seq", sa.Integer(), nullable=False),
        sa.Column("window_hash", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "conversation_id",
            "window_hash",
            "prompt_version",
            "schema_version",
            name="uq_memory_extraction_runs_window",
        ),
        sa.CheckConstraint(
            _one_of("status", ("running", "completed", "failed", "superseded")), name="ck_memory_extraction_runs_status"
        ),
        sa.CheckConstraint("from_seq >= 1 AND to_seq >= from_seq", name="ck_memory_extraction_runs_window"),
        sa.CheckConstraint("attempt >= 1", name="ck_memory_extraction_runs_attempt"),
    )


def _create_memories() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), _fk("characters.id", "RESTRICT"), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), _fk("conversations.id", "RESTRICT"), nullable=True),
        sa.Column("origin_conversation_id", sa.Text(), _fk("conversations.id", "SET NULL"), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("subject_key", sa.Text(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("durability", sa.Text(), nullable=False, server_default="lasting"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("valid_from", sa.Text(), nullable=True),
        sa.Column("valid_to", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("superseded_by_id", sa.Text(), _fk("memories.id", "SET NULL"), nullable=True),
        sa.Column("conflict_group_id", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.Text(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(_one_of("scope", ("conversation", "shared")), name="ck_memories_scope"),
        # A conversation memory names its conversation and a shared one never does.
        sa.CheckConstraint("(scope = 'conversation') = (conversation_id IS NOT NULL)", name="ck_memories_scope_owner"),
        sa.CheckConstraint(_one_of("kind", _MEMORY_KINDS), name="ck_memories_kind"),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 280", name="ck_memories_content"),
        sa.CheckConstraint(_one_of("provenance", _PROVENANCES), name="ck_memories_provenance"),
        sa.CheckConstraint(_one_of("durability", ("lasting", "temporary")), name="ck_memories_durability"),
        sa.CheckConstraint(_one_of("status", _STATUSES), name="ck_memories_status"),
        sa.CheckConstraint("importance BETWEEN 1 AND 5", name="ck_memories_importance"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_memories_confidence"),
        # docs/memory-engine.md (The memory record): an inference is at most 0.6, a confirmation is 1.0, and a temporary
        # memory always has an expiry.
        sa.CheckConstraint("provenance <> 'inferred' OR confidence <= 0.6", name="ck_memories_inferred_confidence"),
        sa.CheckConstraint(
            "provenance <> 'user_confirmed' OR confidence = 1.0", name="ck_memories_confirmed_confidence"
        ),
        sa.CheckConstraint("durability <> 'temporary' OR expires_at IS NOT NULL", name="ck_memories_temporary_expiry"),
        sa.CheckConstraint("locked IN (0, 1)", name="ck_memories_locked"),
        sa.CheckConstraint("needs_review IN (0, 1)", name="ck_memories_needs_review"),
        sa.CheckConstraint("version >= 1", name="ck_memories_version"),
        sa.CheckConstraint("use_count >= 0", name="ck_memories_use_count"),
        sa.CheckConstraint("length(language) > 0", name="ck_memories_language"),
    )
    op.create_index("ix_memories_scope", "memories", ["character_id", "scope", "conversation_id", "status"])
    op.create_index(
        "ix_memories_subject",
        "memories",
        ["character_id", "conversation_id", "subject_key"],
        sqlite_where=sa.text("subject_key IS NOT NULL"),
    )
    # The fingerprint is unique per owner (a conversation, or the shared scope) among the memories in use.
    op.execute(
        "CREATE UNIQUE INDEX uq_memories_fingerprint ON memories "
        "(character_id, ifnull(conversation_id, 'shared'), fingerprint) WHERE status IN ('active', 'uncertain')"
    )

    op.create_table(
        "memory_revisions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("memory_id", sa.Text(), _fk("memories.id", "CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), _fk("memory_extraction_runs.id", "SET NULL"), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("memory_id", "version", name="uq_memory_revisions_version"),
        sa.CheckConstraint("version >= 1", name="ck_memory_revisions_version"),
        sa.CheckConstraint("json_valid(snapshot_json)", name="ck_memory_revisions_snapshot_json"),
        sa.CheckConstraint(_one_of("actor", ("extraction", "user", "consistency")), name="ck_memory_revisions_actor"),
        sa.CheckConstraint("length(reason) > 0", name="ck_memory_revisions_reason"),
    )
    op.execute(
        "CREATE TRIGGER memory_revisions_no_update BEFORE UPDATE ON memory_revisions "
        "BEGIN SELECT RAISE(ABORT, 'memory_revisions is append-only'); END"
    )

    op.create_table(
        "memory_sources",
        sa.Column("memory_id", sa.Text(), _fk("memories.id", "CASCADE"), nullable=False),
        sa.Column("message_id", sa.Text(), _fk("messages.id", "RESTRICT"), nullable=False),
        sa.Column("variant_id", sa.Text(), _fk("message_variants.id", "RESTRICT"), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("memory_id", "message_id", "variant_id"),
    )
    # Finds what a message edit affects.
    op.create_index("ix_memory_sources_message", "memory_sources", ["message_id"])

    op.create_table(
        "memory_tombstones",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), _fk("characters.id", "RESTRICT"), nullable=False),
        sa.Column("conversation_id", sa.Text(), _fk("conversations.id", "SET NULL"), nullable=True),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("subject_key", sa.Text(), nullable=True),
        sa.Column("value_hash", sa.Text(), nullable=True),
        sa.Column("source_hashes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_max_seq", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("json_valid(source_hashes_json)", name="ck_memory_tombstones_source_hashes_json"),
        sa.CheckConstraint("source_max_seq >= 1", name="ck_memory_tombstones_source_max_seq"),
    )
    op.create_index(
        "ix_memory_tombstones_lookup", "memory_tombstones", ["character_id", "conversation_id", "fingerprint"]
    )


def _create_episodes() -> None:
    op.create_table(
        "episodic_memories",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), _fk("characters.id", "RESTRICT"), nullable=False),
        sa.Column("conversation_id", sa.Text(), _fk("conversations.id", "RESTRICT"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("participants_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 120", name="ck_episodic_memories_title"),
        sa.CheckConstraint("length(description) BETWEEN 1 AND 600", name="ck_episodic_memories_description"),
        sa.CheckConstraint("json_valid(participants_json)", name="ck_episodic_memories_participants_json"),
        sa.CheckConstraint("importance BETWEEN 1 AND 5", name="ck_episodic_memories_importance"),
        sa.CheckConstraint(_one_of("status", ("active", "invalidated")), name="ck_episodic_memories_status"),
        sa.CheckConstraint("locked IN (0, 1)", name="ck_episodic_memories_locked"),
    )
    op.create_index(
        "uq_episodic_memories_fingerprint",
        "episodic_memories",
        ["conversation_id", "fingerprint"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_episodic_memories_list", "episodic_memories", ["character_id", "conversation_id", "occurred_at"]
    )

    op.create_table(
        "episodic_sources",
        sa.Column("episode_id", sa.Text(), _fk("episodic_memories.id", "RESTRICT"), nullable=False),
        sa.Column("message_id", sa.Text(), _fk("messages.id", "RESTRICT"), nullable=False),
        sa.Column("variant_id", sa.Text(), _fk("message_variants.id", "RESTRICT"), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("episode_id", "message_id", "variant_id"),
    )
    op.create_index("ix_episodic_sources_message", "episodic_sources", ["message_id"])

    op.create_table(
        "episode_links",
        sa.Column("from_episode_id", sa.Text(), _fk("episodic_memories.id", "RESTRICT"), nullable=False),
        sa.Column("to_episode_id", sa.Text(), _fk("episodic_memories.id", "RESTRICT"), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("from_episode_id", "to_episode_id", "relation"),
        sa.CheckConstraint(_one_of("relation", ("causes", "follows", "related")), name="ck_episode_links_relation"),
        sa.CheckConstraint("from_episode_id <> to_episode_id", name="ck_episode_links_distinct"),
    )
    op.create_index("ix_episode_links_to", "episode_links", ["to_episode_id"])


def _create_relationship() -> None:
    op.create_table(
        "character_state_history",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("conversation_id", sa.Text(), _fk("conversations.id", "RESTRICT"), nullable=False),
        sa.Column("character_id", sa.Text(), _fk("characters.id", "RESTRICT"), nullable=False),
        sa.Column("event_kind", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=True),
        sa.Column("delta", sa.Integer(), nullable=True),
        sa.Column("value_before", sa.Text(), nullable=True),
        sa.Column("value_after", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_message_ids_json", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), _fk("memory_extraction_runs.id", "SET NULL"), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="applied"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(_one_of("event_kind", _EVENT_KINDS), name="ck_character_state_history_event_kind"),
        sa.CheckConstraint(
            "dimension IS NULL OR " + _one_of("dimension", _DIMENSIONS), name="ck_character_state_history_dimension"
        ),
        sa.CheckConstraint("length(reason) > 0", name="ck_character_state_history_reason"),
        sa.CheckConstraint(
            "json_valid(source_message_ids_json)", name="ck_character_state_history_source_message_ids_json"
        ),
        # Every event cites its messages, except a correction the user made directly.
        sa.CheckConstraint(
            "event_kind = 'user_correction' OR source_message_ids_json <> '[]'",
            name="ck_character_state_history_sources",
        ),
        sa.CheckConstraint(_one_of("status", ("applied", "invalidated")), name="ck_character_state_history_status"),
    )
    op.create_index(
        "ix_character_state_history_conversation", "character_state_history", ["conversation_id", "created_at"]
    )

    op.create_table(
        "relationship_states",
        sa.Column("conversation_id", sa.Text(), _fk("conversations.id", "RESTRICT"), primary_key=True),
        sa.Column("familiarity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trust", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tension", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("narrative", sa.Text(), nullable=False, server_default=""),
        sa.Column("expectations", sa.Text(), nullable=False, server_default=""),
        sa.Column("boundaries", sa.Text(), nullable=False, server_default=""),
        sa.Column("emotional_state", sa.Text(), nullable=True),
        sa.Column("current_topic", sa.Text(), nullable=True),
        sa.Column("open_threads_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("goals_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("recent_event_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("narrative_changes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("last_history_id", sa.Text(), _fk("character_state_history.id", "SET NULL"), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "familiarity BETWEEN 0 AND 100 AND trust BETWEEN 0 AND 100 AND confidence BETWEEN 0 AND 100 "
            "AND tension BETWEEN 0 AND 100",
            name="ck_relationship_states_dimensions",
        ),
        sa.CheckConstraint(
            "json_valid(open_threads_json) AND json_valid(goals_json) AND json_valid(recent_event_ids_json) "
            "AND json_valid(narrative_changes_json)",
            name="ck_relationship_states_json",
        ),
    )


def _create_search() -> None:
    op.execute(
        f"CREATE VIRTUAL TABLE memory_fts USING fts5(memory_id UNINDEXED, content_stem, subject_stem, {_TOKENIZER})"
    )
    op.execute(
        f"CREATE VIRTUAL TABLE episode_fts USING fts5(episode_id UNINDEXED, title_stem, description_stem, {_TOKENIZER})"
    )


def upgrade() -> None:
    _create_runs()
    _create_memories()
    _create_episodes()
    _create_relationship()
    _create_search()


def downgrade() -> None:
    op.execute("DROP TABLE episode_fts")
    op.execute("DROP TABLE memory_fts")
    op.drop_table("relationship_states")
    op.drop_index("ix_character_state_history_conversation", table_name="character_state_history")
    op.drop_table("character_state_history")
    op.drop_index("ix_episode_links_to", table_name="episode_links")
    op.drop_table("episode_links")
    op.drop_index("ix_episodic_sources_message", table_name="episodic_sources")
    op.drop_table("episodic_sources")
    op.drop_index("ix_episodic_memories_list", table_name="episodic_memories")
    op.drop_index("uq_episodic_memories_fingerprint", table_name="episodic_memories")
    op.drop_table("episodic_memories")
    op.drop_index("ix_memory_tombstones_lookup", table_name="memory_tombstones")
    op.drop_table("memory_tombstones")
    op.drop_index("ix_memory_sources_message", table_name="memory_sources")
    op.drop_table("memory_sources")
    op.execute("DROP TRIGGER memory_revisions_no_update")
    op.drop_table("memory_revisions")
    op.execute("DROP INDEX uq_memories_fingerprint")
    op.drop_index("ix_memories_subject", table_name="memories")
    op.drop_index("ix_memories_scope", table_name="memories")
    op.drop_table("memories")
    op.drop_table("memory_extraction_runs")
