"""Tests that the committed OpenAPI document is the one the application serves."""

import json
from pathlib import Path

import pytest

from lyrveil.openapi_document import build_document, main, render_document

COMMITTED = Path(__file__).resolve().parents[2] / "openapi.json"
REGENERATE = (
    "docker build -f backend/Dockerfile --target openapi --output type=local,dest=backend . "
    "(then run npm run api:generate --prefix frontend)"
)


def test_the_committed_document_matches_the_application() -> None:
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed == build_document(), f"backend/openapi.json is out of date. Regenerate it with: {REGENERATE}"


def test_the_rendering_is_stable_and_ends_with_one_newline() -> None:
    text = render_document(build_document())
    assert text == render_document(json.loads(text))
    assert text.endswith("}\n")
    assert "\r" not in text


def test_the_script_writes_the_rendered_document(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    assert main(["openapi_document", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == render_document(build_document())


def test_the_script_asks_for_a_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["openapi_document"]) == 2
    assert "usage:" in capsys.readouterr().err
