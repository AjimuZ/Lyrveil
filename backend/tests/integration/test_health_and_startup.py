"""Checks the health routes and the startup of `main()` against real database files."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

import lyrveil.main as backend
from lyrveil.logs import configure_logging
from lyrveil.main import EXIT_DATABASE_UNAVAILABLE, create_app
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

TOKEN = "ab" * 32
# The address of the local app with the default port; any other Host is refused.
APP_URL = "http://localhost:4200"


def client_for(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=APP_URL)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    # main() points the root log handler at the captured stdout of the test; later tests must not inherit it.
    yield
    configure_logging("INFO")


def test_live_answers_200_without_a_database(settings: Settings) -> None:
    client = client_for(create_app(settings, engine=None, control_probe=lambda: False))
    response = client.get("/api/v1/health/live")
    assert (response.status_code, response.json()) == (200, {"status": "alive"})


def test_ready_reports_every_check_on_a_migrated_database(settings: Settings, engine: Engine) -> None:
    upgrade_to_head(engine)
    client = client_for(create_app(settings, engine, control_probe=lambda: True))
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": {"ok": True, "journal_mode": "wal"},
            "migrations": {"ok": True, "current": "0001", "head": "0001"},
            "fts5": {"ok": True},
        },
        "control_api": {"reachable": True},
    }


def test_the_control_api_is_reported_apart_from_readiness(settings: Settings, engine: Engine) -> None:
    upgrade_to_head(engine)
    client = client_for(create_app(settings, engine, control_probe=lambda: False))
    body = client.get("/api/v1/health/ready").json()
    assert body["status"] == "ready"
    assert body["control_api"] == {"reachable": False}


def test_ready_answers_503_while_migrations_are_pending(settings: Settings, engine: Engine) -> None:
    client = client_for(create_app(settings, engine, control_probe=lambda: True))
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["migrations"] == {"ok": False, "current": None, "head": "0001"}
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["fts5"]["ok"] is True


def test_ready_answers_503_on_a_database_without_wal(settings: Settings) -> None:
    client = client_for(create_app(settings, create_engine("sqlite://"), control_probe=lambda: True))
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {"ok": False, "journal_mode": "memory"}


def test_ready_answers_503_when_no_database_was_opened(settings: Settings) -> None:
    response = client_for(create_app(settings, engine=None, control_probe=lambda: True)).get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {"ok": False, "journal_mode": None}


def start(monkeypatch: pytest.MonkeyPatch, database_path: Path) -> list[FastAPI]:
    """Runs main() with a stand-in for Uvicorn and returns the applications it was asked to serve."""
    served: list[FastAPI] = []

    def fake_run(app: FastAPI, **_options: Any) -> None:
        served.append(app)

    monkeypatch.setenv("LYRVEIL_CONTROL_TOKEN", TOKEN)
    monkeypatch.setattr(backend, "DATABASE_PATH", database_path)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    return served


def test_main_migrates_a_new_database_and_serves_a_ready_application(
    monkeypatch: pytest.MonkeyPatch, database_path: Path
) -> None:
    served = start(monkeypatch, database_path)
    assert backend.main() == 0
    (app,) = served
    app.state.control_probe = lambda: False
    response = client_for(app).get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["migrations"]["current"] == "0001"
    app.state.engine.dispose()


def test_main_refuses_to_serve_without_wal(
    monkeypatch: pytest.MonkeyPatch, database_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    served = start(monkeypatch, database_path)
    monkeypatch.setattr(backend, "create_database_engine", lambda _path: create_engine("sqlite://"))
    assert backend.main() == EXIT_DATABASE_UNAVAILABLE
    assert served == []
    assert '"error_code": "DB_CAPABILITY_MISSING"' in capsys.readouterr().out


def test_main_refuses_to_serve_when_the_database_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the database folder should be", encoding="utf-8")
    served = start(monkeypatch, blocker / "db" / "lyrveil.sqlite3")
    assert backend.main() == EXIT_DATABASE_UNAVAILABLE
    assert served == []
    assert "database_unavailable" in capsys.readouterr().out
