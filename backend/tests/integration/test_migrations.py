"""Checks that `alembic upgrade head` builds the schema on an empty database, and that the code can do the same."""

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
HEAD = "0003"
TABLES = {"alembic_version", "application_settings", "generation_jobs", "characters", "conversations"}


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
    assert known_revisions() == {BASELINE, JOBS, HEAD}


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
    assert alembic(database_path, "downgrade", JOBS, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == {"alembic_version", "application_settings", "generation_jobs"}
    assert alembic(database_path, "downgrade", BASELINE, cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == {"alembic_version", "application_settings"}
    assert alembic(database_path, "downgrade", "base", cwd=tmp_path).returncode == 0
    assert user_tables(database_path) == {"alembic_version"}


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
