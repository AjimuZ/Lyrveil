"""Tests the FastAPI application factory."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from lyrveil.main import create_app
from lyrveil.settings import Settings


def make_settings() -> Settings:
    return Settings(control_token=SecretStr("ab" * 32))


def test_the_factory_keeps_the_settings_and_serves_openapi_under_the_base_path() -> None:
    settings = make_settings()
    app = create_app(settings)
    assert app.state.settings is settings
    client = TestClient(app)
    assert client.get("/api/v1/openapi.json").json()["info"]["title"] == "Lyrveil"


def test_no_page_loads_scripts_from_a_cdn() -> None:
    client = TestClient(create_app(make_settings()))
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
