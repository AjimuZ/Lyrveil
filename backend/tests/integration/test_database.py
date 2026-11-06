"""Proves, on the SQLite build of the image, that WAL and FTS5 work and that startup refuses a database without them."""

from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError

from lyrveil.persistence.database import (
    WRITE,
    DatabaseCapabilityError,
    create_database_engine,
    read_capabilities,
    verify_capabilities,
)


def test_a_new_database_is_in_wal_mode_with_fts5(engine: Engine, database_path: Path) -> None:
    found = verify_capabilities(engine)
    assert found.journal_mode == "wal"
    assert found.fts5_compiled
    assert found.fts5_usable
    with engine.connect() as connection:
        # The mode is stored in the file, so a second process finds WAL already active.
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO sample DEFAULT VALUES")
        assert database_path.with_name(database_path.name + "-wal").is_file()


def test_every_connection_applies_the_documented_settings(engine: Engine) -> None:
    expected = {
        "journal_mode": "wal",
        "synchronous": 2,
        "foreign_keys": 1,
        "busy_timeout": 5000,
        "wal_autocheckpoint": 1000,
    }
    with engine.connect() as connection:
        found = {name: connection.exec_driver_sql(f"PRAGMA {name}").scalar_one() for name in expected}
    assert found == expected


def test_fts5_matches_stemmed_text_without_accents(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            'CREATE VIRTUAL TABLE memory_fts USING fts5(content_stem, tokenize = "unicode61 remove_diacritics 2")'
        )
        connection.exec_driver_sql("INSERT INTO memory_fts (content_stem) VALUES (?)", ("un caffè caldo",))
        hits = connection.exec_driver_sql("SELECT content_stem FROM memory_fts WHERE memory_fts MATCH ?", ("caffe",))
        assert hits.scalars().all() == ["un caffè caldo"]


def test_a_database_that_cannot_use_wal_is_refused() -> None:
    # An in-memory database answers 'memory' to the WAL request, as a file system without shared memory would
    # answer 'delete'. Reporting ready on it would hide a durability loss.
    memory_engine = create_engine("sqlite://")
    assert read_capabilities(memory_engine).journal_mode == "memory"
    with pytest.raises(DatabaseCapabilityError, match="WAL journal mode") as refusal:
        verify_capabilities(memory_engine)
    assert refusal.value.code == "DB_CAPABILITY_MISSING"


def test_the_write_option_takes_the_write_lock_when_the_transaction_begins(database_path: Path) -> None:
    first = create_database_engine(database_path)
    second = create_database_engine(database_path)
    try:
        with first.connect() as writer, second.connect() as contender:
            driver = contender.connection.driver_connection
            assert driver is not None
            driver.execute("PRAGMA busy_timeout = 0")
            writer.execution_options(**WRITE).exec_driver_sql("SELECT 1")
            with pytest.raises(OperationalError, match="locked"):
                contender.execution_options(**WRITE).exec_driver_sql("SELECT 1")
    finally:
        first.dispose()
        second.dispose()


def test_a_deferred_transaction_leaves_the_write_lock_free(database_path: Path) -> None:
    first = create_database_engine(database_path)
    second = create_database_engine(database_path)
    try:
        with first.connect() as reader, second.connect() as writer:
            reader.exec_driver_sql("SELECT 1")
            assert writer.execution_options(**WRITE).exec_driver_sql("SELECT 1").scalar_one() == 1
    finally:
        first.dispose()
        second.dispose()
