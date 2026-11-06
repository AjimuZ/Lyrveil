"""Fixtures of the integration tests: a real SQLite file on the container's file system."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine

from lyrveil.persistence.database import create_database_engine
from lyrveil.settings import Settings


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    """Returns a database path inside a folder that does not exist yet, as /data/db does on a first start."""
    return tmp_path / "data" / "db" / "lyrveil.sqlite3"


@pytest.fixture
def engine(database_path: Path) -> Iterator[Engine]:
    """Yields the production engine on an empty database file, and disposes it afterwards."""
    created = create_database_engine(database_path)
    yield created
    created.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(control_token=SecretStr("ab" * 32))
