"""Tests the capability rules of the startup check with scripted answers, so each refusal is reached on its own."""

import pytest
from sqlalchemy import create_engine

import lyrveil.persistence.database as database
from lyrveil.persistence.database import Capabilities, DatabaseCapabilityError, verify_capabilities


def scripted(monkeypatch: pytest.MonkeyPatch, found: Capabilities) -> None:
    monkeypatch.setattr(database, "read_capabilities", lambda _engine: found)


def test_wal_and_fts5_together_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    found = Capabilities(journal_mode="wal", fts5_compiled=True, fts5_usable=True)
    scripted(monkeypatch, found)
    assert verify_capabilities(create_engine("sqlite://")) == found


@pytest.mark.parametrize(
    ("found", "message"),
    [
        (Capabilities("delete", True, True), "WAL journal mode (SQLite reports 'delete')"),
        (Capabilities("wal", False, True), "FTS5"),
        (Capabilities("wal", True, False), "FTS5"),
        (Capabilities("memory", False, False), "WAL journal mode (SQLite reports 'memory') and FTS5"),
    ],
)
def test_a_missing_capability_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, found: Capabilities, message: str
) -> None:
    scripted(monkeypatch, found)
    with pytest.raises(DatabaseCapabilityError) as refusal:
        verify_capabilities(create_engine("sqlite://"))
    assert message in str(refusal.value)
    assert refusal.value.code == "DB_CAPABILITY_MISSING"
