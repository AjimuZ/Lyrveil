"""Runs and inspects the Alembic migrations of backend/migrations for a SQLAlchemy engine."""

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

# alembic.ini and migrations/ sit beside src/ in the image (/app) and in the repository (backend/).
PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class MigrationState:
    """The revision a database is at, and the newest revision the code knows."""

    current: str | None
    head: str

    @property
    def up_to_date(self) -> bool:
        """True when the database is at the head revision. A database with no revision is not."""
        return self.current == self.head


def _config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    # Absolute, so the result does not depend on the working directory of the process.
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations").replace("%", "%%"))
    return config


def head_revision() -> str:
    """Returns the newest revision of the migrations folder; raises RuntimeError when the folder has none."""
    head = ScriptDirectory.from_config(_config()).get_current_head()
    if head is None:
        raise RuntimeError("The migrations folder has no revision.")
    return head


def migration_state(engine: Engine) -> MigrationState:
    """Returns the current and head revisions, reading the database without changing it."""
    head = head_revision()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
        connection.rollback()
    return MigrationState(current=current, head=head)


def upgrade_to_head(engine: Engine) -> MigrationState:
    """Applies every pending migration to the database of `engine` and returns the resulting state.

    Running it on a database that is already at head changes nothing. A failing migration raises its error and
    leaves the database as it was, because the whole upgrade runs in one transaction and SQLite DDL is transactional.
    """
    config = _config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return migration_state(engine)
