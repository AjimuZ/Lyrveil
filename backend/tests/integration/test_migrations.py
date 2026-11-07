"""Checks that `alembic upgrade head` builds the schema on an empty database, and that the code can do the same."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import Engine

from lyrveil.persistence.migrations import (
    PROJECT_ROOT,
    head_revision,
    known_revisions,
    migration_state,
    upgrade_to_head,
)

BASELINE = "0001"
JOBS = "0002"
CONVERSATIONS = "0003"
MESSAGES = "0004"
SEARCH = "0005"
CARD = "0006"
VERSIONS = "0007"
ASSETS = "0008"
SCENARIOS = "0009"
MEMORY = "0010"
SUMMARIES = "0011"
ASSEMBLIES = "0012"
IMAGES = "0013"
PROPOSALS = "0014"
HEAD = "0015"
CONVERSATION_TABLES = {"alembic_version", "application_settings", "generation_jobs", "characters", "conversations"}
MESSAGE_TABLES = {*CONVERSATION_TABLES, "messages", "message_variants"}


def fts_tables(*names: str) -> set[str]:
    """Returns the virtual tables `names` with the shadow tables SQLite stores an FTS5 table in."""
    return {f"{name}{suffix}" for name in names for suffix in ("", "_data", "_idx", "_content", "_docsize", "_config")}


SEARCH_TABLES = fts_tables("message_fts", "conversation_fts")
CARD_TABLES = {"character_traits", "character_visual_profiles"}
CARD_TABLES_WITH_VERSIONS = {*CARD_TABLES, "character_versions"}
TABLES_WITH_ASSETS = {*MESSAGE_TABLES, *SEARCH_TABLES, *CARD_TABLES_WITH_VERSIONS, "character_assets"}
TABLES_WITH_SCENARIOS = {*TABLES_WITH_ASSETS, "scenarios"}
MEMORY_TABLES = {
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
    *fts_tables("memory_fts", "episode_fts"),
}
TABLES_WITH_MEMORY = {*TABLES_WITH_SCENARIOS, *MEMORY_TABLES}
TABLES_WITH_SUMMARIES = {*TABLES_WITH_MEMORY, "conversation_summaries"}
TABLES_WITH_ASSEMBLIES = {*TABLES_WITH_SUMMARIES, "prompt_assemblies"}
TABLES_WITH_IMAGES = {*TABLES_WITH_ASSEMBLIES, "generated_images"}
TABLES_WITH_PROPOSALS = {*TABLES_WITH_IMAGES, "image_proposals"}
TABLES = {*TABLES_WITH_PROPOSALS, "model_lifecycle_events"}


def alembic(database_path: Path, *command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Runs the Alembic command line against `database_path`, from a folder that holds no Alembic files."""
    arguments = ["-c", str(PROJECT_ROOT / "alembic.ini"), "-x", f"database={database_path}", *command]
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


def user_tables(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {name for (name,) in rows}


def test_the_revisions_follow_one_another() -> None:
    assert head_revision() == HEAD
    assert known_revisions() == {
        BASELINE,
        JOBS,
        CONVERSATIONS,
        MESSAGES,
        SEARCH,
        CARD,
        VERSIONS,
        ASSETS,
        SCENARIOS,
        MEMORY,
        SUMMARIES,
        ASSEMBLIES,
        IMAGES,
        PROPOSALS,
        HEAD,
    }


def test_upgrade_head_creates_the_schema_on_an_empty_database(database_path: Path, tmp_path: Path) -> None:
    result = alembic(database_path, "upgrade", "head", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert user_tables(database_path) == TABLES
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert [row[1] for row in connection.execute("PRAGMA table_info(application_settings)")] == [
            "key",
            "value_json",
            "updated_at",
        ]


def test_the_baseline_refuses_a_setting_that_is_not_json(database_path: Path, tmp_path: Path) -> None:
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    with sqlite3.connect(database_path) as connection:
        connection.execute("INSERT INTO application_settings VALUES ('a', '{\"x\": 1}', '2026-11-06T10:00:00.000Z')")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute("INSERT INTO application_settings VALUES ('b', 'not json', '2026-11-06T10:00:00.000Z')")


def test_upgrading_twice_changes_nothing_and_downgrading_removes_the_tables(
    database_path: Path, tmp_path: Path
) -> None:
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == TABLES
    assert alembic(database_path, "downgrade", PROPOSALS, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == TABLES_WITH_PROPOSALS
    assert alembic(database_path, "downgrade", ASSEMBLIES, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == TABLES_WITH_ASSEMBLIES
    assert alembic(database_path, "downgrade", SUMMARIES, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == TABLES_WITH_SUMMARIES
    assert alembic(database_path, "downgrade", MEMORY, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == TABLES_WITH_MEMORY
    assert alembic(database_path, "downgrade", SCENARIOS, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == TABLES_WITH_SCENARIOS
    assert alembic(database_path, "downgrade", ASSETS, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == TABLES_WITH_ASSETS
    assert alembic(database_path, "downgrade", VERSIONS, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == {*MESSAGE_TABLES, *SEARCH_TABLES, *CARD_TABLES_WITH_VERSIONS}
    assert alembic(database_path, "downgrade", CARD, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == {*MESSAGE_TABLES, *SEARCH_TABLES, *CARD_TABLES}
    assert alembic(database_path, "downgrade", SEARCH, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == {*MESSAGE_TABLES, *SEARCH_TABLES}
    assert alembic(database_path, "downgrade", MESSAGES, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == MESSAGE_TABLES
    assert alembic(database_path, "downgrade", CONVERSATIONS, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == CONVERSATION_TABLES
    assert alembic(database_path, "downgrade", JOBS, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == {"alembic_version", "application_settings", "generation_jobs"}
    assert alembic(database_path, "downgrade", BASELINE, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == {"alembic_version", "application_settings"}
    assert alembic(database_path, "downgrade", "base", cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == {"alembic_version"}


def test_existing_characters_get_their_first_version_and_versions_are_append_only(
    database_path: Path, tmp_path: Path
) -> None:
    assert alembic(database_path, "upgrade", CARD, cwd=tmp_path).returncode == 0
    now = "2026-11-07T10:00:00.000Z"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO characters (id, first_name, age, created_at, updated_at) VALUES ('c', 'Alba', 30, ?, ?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO character_visual_profiles (character_id, hair, updated_at) VALUES ('c', 'Rossi', ?)", (now,)
        )
        connection.execute(
            "INSERT INTO character_traits (id, character_id, category, description, sort_order, locked, "
            "created_at, updated_at) VALUES ('t2', 'c', 'fear', 'Il buio', 1, 0, ?, ?), "
            "('t1', 'c', 'main_trait', 'Ironica', 0, 1, ?, ?)",
            (now, now, now, now),
        )
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT version_no, actor, snapshot_json FROM character_versions").fetchall()
        assert [(number, actor) for number, actor, _ in row] == [(1, "user")]
        snapshot = json.loads(row[0][2])
        assert snapshot["identity"]["first_name"] == "Alba"
        assert snapshot["traits"] == [
            {"category": "main_trait", "description": "Ironica", "locked": True},
            {"category": "fear", "description": "Il buio", "locked": False},
        ]
        assert snapshot["visual"]["hair"] == "Rossi"
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE character_versions SET change_summary = 'x'")


def test_the_job_table_refuses_unknown_values_and_duplicate_idempotency_keys(
    database_path: Path, tmp_path: Path
) -> None:
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    columns = "id, kind, status, stage, required_mode, priority, payload_json, attempt, max_attempts, created_at"
    insert = f"INSERT INTO generation_jobs ({columns}, idempotency_key) VALUES (?, ?, ?, 'run', ?, 10, ?, 0, 2, ?, ?)"
    now = "2026-11-07T10:00:00.000Z"
    with sqlite3.connect(database_path) as connection:
        connection.execute(insert, ("a", "chat_reply", "queued", "llm", "{}", now, "k"))
        connection.execute(insert, ("b", "chat_reply", "queued", "llm", "{}", now, None))
        connection.execute(insert, ("c", "chat_reply", "queued", "llm", "{}", now, None))
        for row in (
            ("d", "dance", "queued", "llm", "{}", now, None),
            ("e", "chat_reply", "paused", "llm", "{}", now, None),
            ("f", "chat_reply", "queued", "gpu", "{}", now, None),
            ("g", "chat_reply", "queued", "llm", "not json", now, None),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                connection.execute(insert, row)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(insert, ("h", "chat_reply", "queued", "llm", "{}", now, "k"))


def test_the_code_migrates_an_engine_and_reports_the_state(engine: Engine, database_path: Path) -> None:
    before = migration_state(engine)
    assert (before.current, before.head, before.up_to_date) == (None, HEAD, False)
    after = upgrade_to_head(engine)
    assert (after.current, after.head, after.up_to_date) == (HEAD, HEAD, True)
    assert upgrade_to_head(engine) == after
    assert user_tables(database_path) == TABLES


NOW = "2026-11-07T10:00:00.000Z"


def open_database(database_path: Path) -> sqlite3.Connection:
    """Opens the database the way the service does for foreign keys, so RESTRICT and CASCADE really apply."""
    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def seed_chat(connection: sqlite3.Connection) -> None:
    """Inserts a character with one conversation, one message and its variant, with the ids c, v, m and a."""
    connection.execute(
        "INSERT INTO characters (id, first_name, age, created_at, updated_at) VALUES ('c', 'Alba', 30, ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO conversations (id, character_id, title, created_at, updated_at) VALUES ('v', 'c', 'Faro', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO messages (id, conversation_id, seq, author, status, content, created_at) "
        "VALUES ('m', 'v', 1, 'user', 'complete', 'Lavoro allo Studio Rossi', ?)",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO message_variants (id, message_id, variant_no, content, content_hash, source, status, created_at) "
        "VALUES ('a', 'm', 1, 'Lavoro allo Studio Rossi', 'h', 'user', 'complete', ?)",
        (NOW,),
    )


def insert_memory(connection: sqlite3.Connection, memory_id: str, **overrides: object) -> None:
    """Inserts a valid conversation memory of character c, changing the columns named in `overrides`."""
    row: dict[str, object] = {
        "id": memory_id,
        "character_id": "c",
        "scope": "conversation",
        "conversation_id": "v",
        "kind": "user_fact",
        "content": "Lavora allo Studio Rossi.",
        "provenance": "stated",
        "importance": 3,
        "confidence": 0.95,
        "fingerprint": f"fp-{memory_id}",
        "language": "it",
        "created_at": NOW,
        "updated_at": NOW,
        **overrides,
    }
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    connection.execute(f"INSERT INTO memories ({columns}) VALUES ({marks})", tuple(row.values()))


def test_the_memory_tables_are_added_to_a_database_at_the_p03_schema(database_path: Path, tmp_path: Path) -> None:
    assert alembic(database_path, "upgrade", SCENARIOS, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == TABLES_WITH_SCENARIOS
    with open_database(database_path) as connection:
        seed_chat(connection)
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == TABLES
    with open_database(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
        assert connection.execute("SELECT first_name FROM characters").fetchall() == [("Alba",)]
        assert connection.execute("SELECT content FROM messages").fetchall() == [("Lavoro allo Studio Rossi",)]
        # The triggers of revision 0005 survive: the message is still indexed.
        assert connection.execute(
            "SELECT message_id FROM message_fts WHERE message_fts MATCH '\"studio\"'"
        ).fetchall() == [("m",)]
        for table in ("memories", "episodic_memories", "relationship_states", "memory_fts", "episode_fts"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"scope": "shared"}, id="shared memory with a conversation"),
        pytest.param({"scope": "conversation", "conversation_id": None}, id="conversation memory without one"),
        pytest.param({"scope": "global"}, id="unknown scope"),
        pytest.param({"kind": "rumor"}, id="unknown kind"),
        pytest.param({"status": "deleted"}, id="unknown status"),
        pytest.param({"provenance": "guessed"}, id="unknown provenance"),
        pytest.param({"content": "x" * 281}, id="content over 280"),
        pytest.param({"content": ""}, id="empty content"),
        pytest.param({"importance": 6}, id="importance over 5"),
        pytest.param({"confidence": 1.2}, id="confidence over 1"),
        pytest.param({"provenance": "inferred", "confidence": 0.7}, id="inference over 0.6"),
        pytest.param({"provenance": "user_confirmed", "confidence": 0.95}, id="confirmation below 1"),
        pytest.param({"durability": "temporary"}, id="temporary without expiry"),
        pytest.param({"locked": 2}, id="locked is not a boolean"),
        pytest.param({"language": ""}, id="empty language"),
    ],
)
def test_a_memory_that_breaks_a_documented_rule_is_refused(
    database_path: Path, tmp_path: Path, overrides: dict[str, object]
) -> None:
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    with open_database(database_path) as connection:
        seed_chat(connection)
        insert_memory(connection, "ok")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            insert_memory(connection, "bad", **overrides)


def test_memories_have_unique_fingerprints_per_owner_while_in_use(database_path: Path, tmp_path: Path) -> None:
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    with open_database(database_path) as connection:
        seed_chat(connection)
        insert_memory(connection, "first", fingerprint="same")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert_memory(connection, "twin", fingerprint="same", status="uncertain")
        # A superseded memory leaves the fingerprint free, and the shared scope is a separate owner.
        insert_memory(connection, "old", fingerprint="same", status="superseded")
        insert_memory(connection, "shared", fingerprint="same", scope="shared", conversation_id=None)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert_memory(connection, "shared-twin", fingerprint="same", scope="shared", conversation_id=None)
        # A temporary memory with an expiry and an inference at the cap are valid.
        insert_memory(connection, "t", durability="temporary", expires_at=NOW)
        insert_memory(connection, "i", provenance="inferred", confidence=0.6)


def test_revisions_and_sources_follow_their_memory_and_protect_the_messages(
    database_path: Path, tmp_path: Path
) -> None:
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    with open_database(database_path) as connection:
        seed_chat(connection)
        insert_memory(connection, "mem")
        connection.execute(
            "INSERT INTO memory_revisions (id, memory_id, version, snapshot_json, actor, reason, created_at) "
            "VALUES ('r1', 'mem', 1, '{}', 'user', 'edit', ?)",
            (NOW,),
        )
        connection.execute("INSERT INTO memory_sources VALUES ('mem', 'm', 'a', 'h')")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE memory_revisions SET reason = 'other'")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                "INSERT INTO memory_revisions (id, memory_id, version, snapshot_json, actor, reason, created_at) "
                "VALUES ('r2', 'mem', 1, '{}', 'user', 'edit', ?)",
                (NOW,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(
                "INSERT INTO memory_revisions (id, memory_id, version, snapshot_json, actor, reason, created_at) "
                "VALUES ('r3', 'mem', 2, 'not json', 'user', 'edit', ?)",
                (NOW,),
            )
        # The source message cannot disappear under a memory; the memory takes its revisions and sources with it.
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("DELETE FROM message_variants WHERE id = 'a'")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("DELETE FROM conversations WHERE id = 'v'")
        connection.execute("DELETE FROM memories WHERE id = 'mem'")
        assert connection.execute("SELECT count(*) FROM memory_revisions").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM memory_sources").fetchone() == (0,)


def test_state_events_need_sources_unless_the_user_corrected_them_and_runs_are_unique(
    database_path: Path, tmp_path: Path
) -> None:
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    insert = (
        "INSERT INTO character_state_history (id, conversation_id, character_id, event_kind, reason, "
        "source_message_ids_json, created_at) VALUES (?, 'v', 'c', ?, 'motivo', ?, ?)"
    )
    run = (
        "INSERT INTO memory_extraction_runs (id, conversation_id, from_seq, to_seq, window_hash, prompt_version, "
        "schema_version, status, created_at) VALUES (?, 'v', 1, ?, 'w', 'p1', '1', ?, ?)"
    )
    with open_database(database_path) as connection:
        seed_chat(connection)
        connection.execute(insert, ("h1", "relationship_change", '["m"]', NOW))
        connection.execute(insert, ("h2", "user_correction", "[]", NOW))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(insert, ("h3", "relationship_change", "[]", NOW))
        connection.execute(run, ("run1", 1, "completed", NOW))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(run, ("run2", 2, "completed", NOW))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(run.replace("'w'", "'w2'"), ("run3", 0, "completed", NOW))
        connection.execute(
            "INSERT INTO relationship_states (conversation_id, last_history_id, updated_at) VALUES ('v', 'h1', ?)",
            (NOW,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute("UPDATE relationship_states SET trust = 101")


def test_the_memory_index_folds_accents_and_finds_stems(database_path: Path, tmp_path: Path) -> None:
    assert alembic(database_path, "upgrade", "head", cwd=tmp_path).returncode == 0
    with open_database(database_path) as connection:
        connection.execute(
            "INSERT INTO memory_fts (memory_id, content_stem, subject_stem) "
            "VALUES ('a', 'citta lavor', 'user employer')"
        )
        connection.execute(
            "INSERT INTO episode_fts (episode_id, title_stem, description_stem) VALUES ('e', 'notte', 'faro citta')"
        )
        found = connection.execute("SELECT memory_id FROM memory_fts WHERE memory_fts MATCH '\"città\"'").fetchall()
        assert found == [("a",)]
        found = connection.execute("SELECT episode_id FROM episode_fts WHERE episode_fts MATCH '\"CITTÀ\"'").fetchall()
        assert found == [("e",)]
