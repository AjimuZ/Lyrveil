"""Checks that the environment of the image holds exactly the versions that uv.lock pins, and nothing else."""

import re
import tomllib
from importlib import metadata
from pathlib import Path

LOCK_FILE = Path(__file__).resolve().parents[2] / "uv.lock"


def normalize(name: str) -> str:
    """Returns the package name normalized as in PEP 503, the form uv.lock uses."""
    return re.sub(r"[-_.]+", "-", name).lower()


def test_every_installed_distribution_is_pinned_by_the_lock() -> None:
    lock = tomllib.loads(LOCK_FILE.read_text(encoding="utf-8"))
    pinned = {normalize(package["name"]): package["version"] for package in lock["package"]}
    installed = {normalize(dist.metadata["Name"]): dist.version for dist in metadata.distributions()}
    assert installed, "no distribution is installed"
    assert {name: version for name, version in installed.items() if pinned.get(name) != version} == {}


def test_the_direct_dependencies_have_the_documented_versions() -> None:
    expected = {
        "fastapi": "0.162.0",
        "httpx": "0.28.1",
        "pydantic": "2.14.2",
        "pydantic-settings": "2.16.2",
        "snowballstemmer": "3.1.1",
        "structlog": "26.1.0",
        "tzdata": "2026.6",
        "uvicorn": "0.54.0",
        "websockets": "17.4",
    }
    assert {name: metadata.version(name) for name in expected} == expected
