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


def test_the_test_tools_have_the_documented_versions() -> None:
    expected = {"hypothesis": "6.170.14", "mypy": "2.4.1", "pytest": "9.2.0", "ruff": "0.17.3"}
    assert {name: metadata.version(name) for name in expected} == expected
