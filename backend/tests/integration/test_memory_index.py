"""Checks the stemmed index rows of memories and episodes on a migrated database, and the character purge."""

from sqlalchemy import Connection, Engine, text

from lyrveil.persistence.characters import purge
from lyrveil.persistence.memory_index import (
    index_episode,
    index_memory,
    match_memory_ids,
    unindex_episode,
    unindex_memory,
)
from lyrveil.persistence.migrations import upgrade_to_head

NOW = "2026-11-07T10:00:00.000Z"
MEMORY_TABLES = (
    "memories",
    "memory_revisions",
    "memory_sources",
    "memory_tombstones",
    "memory_extraction_runs",
    "episodic_memories",
    "episodic_sources",
    "episode_links",
    "character_state_history",
    "relationship_states",
    "memory_fts",
    "episode_fts",
)


def count(connection: Connection, table: str) -> int:
    return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def seed_character(connection: Connection, character: str) -> None:
    """Gives `character` a conversation `<id>-chat` with one message, and memories, episodes and state, all indexed."""
    chat, message, variant = f"{character}-chat", f"{character}-m", f"{character}-v"
    params = {"c": character, "chat": chat, "m": message, "v": variant, "now": NOW}
    for sql in (
        "INSERT INTO characters (id, first_name, age, created_at, updated_at) VALUES (:c, 'Alba', 30, :now, :now)",
        "INSERT INTO conversations (id, character_id, title, created_at, updated_at) "
        "VALUES (:chat, :c, 'T', :now, :now)",
        "INSERT INTO messages (id, conversation_id, seq, author, status, created_at) "
        "VALUES (:m, :chat, 1, 'user', 'complete', :now)",
        "INSERT INTO message_variants (id, message_id, variant_no, content_hash, source, status, created_at) "
        "VALUES (:v, :m, 1, 'h', 'user', 'complete', :now)",
        "INSERT INTO memory_extraction_runs (id, conversation_id, from_seq, to_seq, window_hash, prompt_version, "
        "schema_version, status, created_at) VALUES (:c || '-run', :chat, 1, 1, 'w', 'p', '1', 'completed', :now)",
        "INSERT INTO memories (id, character_id, scope, conversation_id, kind, content, provenance, importance, "
        "confidence, fingerprint, language, created_at, updated_at) VALUES (:c || '-1', :c, 'conversation', :chat, "
        "'user_fact', 'Lavora a Milano', 'stated', 3, 0.95, :c || '-fp1', 'it', :now, :now)",
        "INSERT INTO memories (id, character_id, scope, kind, content, provenance, importance, confidence, "
        "superseded_by_id, fingerprint, language, status, created_at, updated_at) VALUES (:c || '-2', :c, 'shared', "
        "'user_fact', 'Lavorava a Roma', 'stated', 3, 0.95, :c || '-1', :c || '-fp2', 'it', 'superseded', :now, :now)",
        "INSERT INTO memory_revisions (id, memory_id, version, snapshot_json, actor, reason, run_id, created_at) "
        "VALUES (:c || '-rev', :c || '-1', 1, '{}', 'user', 'edit', :c || '-run', :now)",
        "INSERT INTO memory_sources VALUES (:c || '-1', :m, :v, 'h')",
        "INSERT INTO memory_tombstones (id, character_id, conversation_id, fingerprint, source_max_seq, created_at) "
        "VALUES (:c || '-tomb', :c, :chat, 'fp', 1, :now)",
        "INSERT INTO episodic_memories (id, character_id, conversation_id, title, description, occurred_at, "
        "importance, fingerprint, created_at, updated_at) "
        "VALUES (:c || '-e1', :c, :chat, 'Faro', 'La notte al faro', :now, 3, 'f1', :now, :now), "
        "(:c || '-e2', :c, :chat, 'Porto', 'La mappa', :now, 3, 'f2', :now, :now)",
        "INSERT INTO episodic_sources VALUES (:c || '-e1', :m, :v, 'h')",
        "INSERT INTO episode_links VALUES (:c || '-e1', :c || '-e2', 'follows')",
        "INSERT INTO character_state_history (id, conversation_id, character_id, event_kind, reason, "
        "source_message_ids_json, run_id, created_at) VALUES (:c || '-h', :chat, :c, 'relationship_change', 'r', "
        "'[\"x\"]', :c || '-run', :now)",
        "INSERT INTO relationship_states (conversation_id, last_history_id, updated_at) "
        "VALUES (:chat, :c || '-h', :now)",
    ):
        connection.execute(text(sql), params)
    index_memory(connection, f"{character}-1", "Lavora a Milano", "user.city", "it")
    index_memory(connection, f"{character}-2", "Lavorava a Roma", "user.city", "it")
    index_episode(connection, f"{character}-e1", "Faro", "La notte al faro", "it")


def test_a_memory_is_found_by_another_form_of_its_words_and_without_accents(engine: Engine) -> None:
    upgrade_to_head(engine)
    with engine.begin() as connection:
        index_memory(connection, "a", "Si è trasferito in città", "user.city", "it")
        index_memory(connection, "b", "Ha due gatti neri", None, "it")
        index_memory(connection, "c", "Ama le passeggiate sul lungomare", "user.hobby", "it")
        assert match_memory_ids(connection, "citta", "it", 10) == ["a"]
        assert match_memory_ids(connection, "Quale CITTÀ?", "it", 10) == ["a"]
        assert match_memory_ids(connection, "il gatto", "it", 10) == ["b"]
        assert match_memory_ids(connection, "hobby", "it", 10) == ["c"]
        assert match_memory_ids(connection, "il la di", "it", 10) == []
        assert match_memory_ids(connection, "", "it", 10) == []
        # An operator in the user's text is a plain word, so both rows match instead of one being excluded.
        assert set(match_memory_ids(connection, "gatto AND NOT città", "it", 10)) == {"a", "b"}


def test_reindexing_replaces_the_row_and_removing_it_forgets_the_memory(engine: Engine) -> None:
    upgrade_to_head(engine)
    with engine.begin() as connection:
        index_memory(connection, "a", "Lavora a Milano", None, "it")
        index_memory(connection, "a", "Vive a Torino", None, "it")
        assert count(connection, "memory_fts") == 1
        assert match_memory_ids(connection, "Milano", "it", 10) == []
        assert match_memory_ids(connection, "Torino", "it", 10) == ["a"]
        unindex_memory(connection, "a")
        unindex_memory(connection, "never-indexed")
        assert count(connection, "memory_fts") == 0
        index_episode(connection, "e", "Faro", "La notte al faro", "it")
        index_episode(connection, "e", "Porto", "La mappa", "it")
        assert count(connection, "episode_fts") == 1
        unindex_episode(connection, "e")
        assert count(connection, "episode_fts") == 0


def test_the_limit_caps_the_matches(engine: Engine) -> None:
    upgrade_to_head(engine)
    with engine.begin() as connection:
        for number in range(5):
            index_memory(connection, f"m{number}", "Ama il mare", None, "it")
        assert len(match_memory_ids(connection, "mare", "it", 3)) == 3


def test_purging_a_character_clears_every_memory_table_and_keeps_the_others(engine: Engine) -> None:
    upgrade_to_head(engine)
    with engine.begin() as connection:
        seed_character(connection, "one")
        seed_character(connection, "two")
        assert all(count(connection, table) > 0 for table in MEMORY_TABLES)
        before = {table: count(connection, table) for table in MEMORY_TABLES}
        purge(connection, "one")
        for table in MEMORY_TABLES:
            assert count(connection, table) == before[table] // 2, table
        assert count(connection, "characters") == 1
        assert match_memory_ids(connection, "Milano", "it", 10) == ["two-1"]
