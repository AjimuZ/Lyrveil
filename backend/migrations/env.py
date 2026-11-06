"""Alembic environment: runs migrations on the connection the backend passes in, or on the database file it opens."""

from pathlib import Path

from alembic import context
from sqlalchemy import Connection

from lyrveil.persistence.database import DEFAULT_DATABASE_PATH, create_database_engine


def _run(connection: Connection) -> None:
    # Hand-written revisions use SQL, so there is no model metadata to compare against.
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
    # SQLite DDL is transactional and Alembic does not manage the transaction here: one commit ends the upgrade.
    connection.commit()


def run_migrations() -> None:
    """Migrates the connection in the config attributes, or else the file from `-x database=` or the default."""
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    given = context.get_x_argument(as_dictionary=True).get("database")
    engine = create_database_engine(Path(given) if given else DEFAULT_DATABASE_PATH)
    try:
        with engine.connect() as opened:
            _run(opened)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported: SQL is always run against the database.")
run_migrations()
