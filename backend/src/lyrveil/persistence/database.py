"""Builds the synchronous SQLite engine of the single writer and verifies that WAL and FTS5 really work."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, Connection
from sqlalchemy.exc import DBAPIError

# Inside the backend container the data volume is mounted at /data (docs/operations.md).
DEFAULT_DATABASE_PATH = Path("/data/db/lyrveil.sqlite3")
DB_CAPABILITY_MISSING = "DB_CAPABILITY_MISSING"
# Execution option that makes a transaction begin with BEGIN IMMEDIATE (ADR 0009), so a writer takes the write lock
# at once instead of failing halfway through a deferred upgrade. Use it as `connection(execution_options=WRITE)`.
WRITE_OPTION = "lyrveil_write"
WRITE: dict[str, Any] = {WRITE_OPTION: True}

# docs/data-model.md (Connection settings). busy_timeout comes first, so the journal mode change can wait for a lock.
CONNECTION_PRAGMAS = (
    "PRAGMA busy_timeout = 5000",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = FULL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA wal_autocheckpoint = 1000",
)
_FTS5_PROBE = "CREATE VIRTUAL TABLE temp.lyrveil_fts5_probe USING fts5(content)"


class DatabaseCapabilityError(Exception):
    """The SQLite build or the storage lacks WAL or FTS5; the operational code is DB_CAPABILITY_MISSING.

    The message lists what is missing and never contains data from the database.
    """

    code = DB_CAPABILITY_MISSING


@dataclass(frozen=True)
class Capabilities:
    """What a connection to the database really provides, as read back from SQLite."""

    journal_mode: str
    fts5_compiled: bool
    fts5_usable: bool

    @property
    def wal(self) -> bool:
        """True when the database file is in WAL mode, which SQLite silently refuses on some file systems."""
        return self.journal_mode == "wal"

    @property
    def fts5(self) -> bool:
        """True when SQLite is compiled with FTS5 and a virtual table of that module can be created."""
        return self.fts5_compiled and self.fts5_usable


def create_database_engine(path: Path) -> Engine:
    """Returns the engine for the SQLite file at `path`, creating its folder.

    Every new connection applies CONNECTION_PRAGMAS. The pysqlite driver is switched to manual transaction control,
    so a transaction begins exactly when SQLAlchemy says so: with BEGIN, or with BEGIN IMMEDIATE when the connection
    carries the WRITE option. Nothing is opened until the first use.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite", database=str(path)))

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: object) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            for pragma in CONNECTION_PRAGMAS:
                cursor.execute(pragma)
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection: Connection) -> None:
        immediate = connection.get_execution_options().get(WRITE_OPTION, False)
        connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    return engine


def read_capabilities(engine: Engine) -> Capabilities:
    """Returns the capabilities read back from a connection of `engine`.

    The journal mode is read, not assumed, because `PRAGMA journal_mode = WAL` answers with the mode that is really
    active. FTS5 is proven by creating a virtual table in the temporary database, which leaves no trace.
    """
    with engine.connect() as connection:
        journal_mode = str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()).lower()
        options = {str(row[0]) for row in connection.exec_driver_sql("PRAGMA compile_options")}
        try:
            connection.exec_driver_sql(_FTS5_PROBE)
            connection.exec_driver_sql("DROP TABLE temp.lyrveil_fts5_probe")
            usable = True
        except DBAPIError:
            usable = False
        connection.rollback()
    return Capabilities(journal_mode=journal_mode, fts5_compiled="ENABLE_FTS5" in options, fts5_usable=usable)


def verify_capabilities(engine: Engine) -> Capabilities:
    """Returns the capabilities of `engine` when WAL and FTS5 both work.

    Raises DatabaseCapabilityError naming each missing capability, so startup stops instead of reporting ready.
    """
    found = read_capabilities(engine)
    missing = []
    if not found.wal:
        missing.append(f"WAL journal mode (SQLite reports '{found.journal_mode}')")
    if not found.fts5:
        missing.append("FTS5")
    if missing:
        raise DatabaseCapabilityError("SQLite lacks " + " and ".join(missing))
    return found
