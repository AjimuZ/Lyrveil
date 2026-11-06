"""Checks that the HTTP fakes serve exactly the endpoints that docs/runtime-contracts.md lists, and nothing else."""

import re
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from fakes.fake_comfy import FakeComfy
from fakes.fake_kobold import FakeKobold
from fakes.scripted_server import Route, ScriptedServer

CONTRACT_NAME = "runtime-contracts.md"
# A table row that starts with an endpoint: "| `GET /api/v1/model` | ...".
ENDPOINT_ROW = re.compile(r"^\| `(GET|POST|WS) ([^`\s]+)` \|")


def contract_text() -> str:
    """Returns the contract page: next to the orchestrator in the repository, or in /app/docs in the test image."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / "docs" / CONTRACT_NAME, here.parents[2] / "docs" / CONTRACT_NAME):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise AssertionError(f"{CONTRACT_NAME} is in neither candidate folder")


def documented_endpoints(section: str) -> set[str]:
    """Returns the endpoints in the rows of the `## <section>` part of the page, such as "POST /prompt".

    The query of a path is not part of the endpoint, so `WS /ws?clientId=<id>` is `WS /ws`.
    """
    found: set[str] = set()
    inside = False
    for line in contract_text().splitlines():
        if line.startswith("## "):
            inside = line.startswith(f"## {section}")
        match = ENDPOINT_ROW.match(line) if inside else None
        if match:
            found.add(f"{match.group(1)} {match.group(2).split('?')[0]}")
    return found


@pytest.fixture(params=[("KoboldCpp", FakeKobold), ("ComfyUI", FakeComfy)], ids=["kobold", "comfy"])
def served(request: pytest.FixtureRequest) -> Iterator[tuple[str, ScriptedServer]]:
    section, fake_type = request.param
    with fake_type() as running:
        yield section, running


def test_the_fake_routes_are_exactly_the_documented_endpoints(served: tuple[str, ScriptedServer]) -> None:
    section, fake = served
    documented = documented_endpoints(section)
    assert documented, f"no endpoint was read from the {section} section: the table format changed"
    assert {route.key for route in fake.ROUTES} == documented


def sample_path(route: Route) -> str:
    return re.sub(r"\{[a-z_]+\}", "sample", route.pattern)


def test_every_documented_route_is_served_and_nothing_else(served: tuple[str, ScriptedServer]) -> None:
    _, fake = served
    for route in fake.ROUTES:
        if route.method == "WS":
            # The handshake itself is tested with ComfyUI's jobs. Without it the path is only known to the router.
            assert httpx.get(f"{fake.url}{sample_path(route)}").status_code == 405
            continue
        url = f"{fake.url}{sample_path(route)}"
        response = httpx.request(route.method, url, params={"filename": "fake_1.png"}, json={"prompt": {"1": {}}})
        assert response.status_code not in {404, 405}, route.key
    for path in ("/", "/queue", "/api/v1/config", "/api/extra/unknown", "/object_info"):
        assert httpx.get(f"{fake.url}{path}").status_code == 404, path
    assert httpx.delete(f"{fake.url}/anything").status_code == 501
