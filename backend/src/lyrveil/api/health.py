"""Health routes: process liveness, and readiness of the database, the migrations and FTS5."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from lyrveil.persistence.database import read_capabilities
from lyrveil.persistence.migrations import head_revision, migration_state

router = APIRouter(tags=["system"])


class LiveReport(BaseModel):
    status: Literal["alive"] = "alive"


class DatabaseCheck(BaseModel):
    ok: bool
    journal_mode: str | None


class MigrationsCheck(BaseModel):
    ok: bool
    current: str | None
    head: str


class Fts5Check(BaseModel):
    ok: bool


class Checks(BaseModel):
    database: DatabaseCheck
    migrations: MigrationsCheck
    fts5: Fts5Check


class ControlApiCheck(BaseModel):
    reachable: bool


class ReadyReport(BaseModel):
    """Readiness of the backend. `control_api` is informational: it never changes `status`.

    The orchestrator may be down while the library, settings and history still work (docs/api.md, System).
    """

    status: Literal["ready", "not_ready"]
    checks: Checks
    control_api: ControlApiCheck


def inspect_database(engine: Engine | None) -> Checks:
    """Returns the three readiness checks for `engine`, which is None when startup never opened a database.

    Never raises for a database problem: a failing check is reported as not ok, without the driver's message.
    """
    head = head_revision()
    if engine is None:
        return Checks(
            database=DatabaseCheck(ok=False, journal_mode=None),
            migrations=MigrationsCheck(ok=False, current=None, head=head),
            fts5=Fts5Check(ok=False),
        )
    try:
        capabilities = read_capabilities(engine)
        state = migration_state(engine)
    except SQLAlchemyError:
        return inspect_database(None)
    return Checks(
        database=DatabaseCheck(ok=capabilities.wal, journal_mode=capabilities.journal_mode),
        migrations=MigrationsCheck(ok=state.up_to_date, current=state.current, head=state.head),
        fts5=Fts5Check(ok=capabilities.fts5),
    )


@router.get("/health/live", response_model=LiveReport, summary="Process alive")
def live() -> LiveReport:
    """Answers 200 whenever the process serves requests. Used by the container health check."""
    return LiveReport()


@router.get(
    "/health/ready",
    response_model=ReadyReport,
    responses={503: {"model": ReadyReport, "description": "A database check failed."}},
    summary="Database, migrations and FTS5 are fine",
)
def ready(request: Request) -> JSONResponse:
    """Answers 200 when the database is in WAL mode, migrated to head and has FTS5, and 503 otherwise.

    The body lists every check, so a failure says which one. The control API is probed too and reported on its own.
    """
    engine: Engine | None = request.app.state.engine
    probe: Callable[[], bool] = request.app.state.control_probe
    checks = inspect_database(engine)
    healthy = checks.database.ok and checks.migrations.ok and checks.fts5.ok
    report = ReadyReport(
        status="ready" if healthy else "not_ready",
        checks=checks,
        control_api=ControlApiCheck(reachable=probe()),
    )
    return JSONResponse(report.model_dump(), status_code=200 if healthy else 503)
