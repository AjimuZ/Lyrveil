"""T-ORC-10: the control API refuses a missing or wrong token on every route except /v1/health.

The application runs in process through Starlette's test client, with a fake GPU probe instead of NVML.
"""

import hmac
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lyrveil_orchestrator.api import create_app
from lyrveil_orchestrator.gpu_monitor import CudaVersion, GpuFacts, GPUResourceMonitor, VramReading

TOKEN = "ab" * 32
# Every route of docs/api.md, Control API, delivered or not, plus paths no phase defines.
GUARDED_ROUTES = [
    ("GET", "/v1/state?since=0&wait=0"),
    ("PUT", "/v1/desired-mode"),
    ("POST", "/v1/leases"),
    ("POST", "/v1/leases/0192f3a0-0000-7000-8000-000000000000/heartbeat"),
    ("DELETE", "/v1/leases/0192f3a0-0000-7000-8000-000000000000"),
    ("POST", "/v1/recover"),
    ("POST", "/v1/drain"),
    ("POST", "/v1/resume"),
    ("GET", "/v1/events?boot_id=x&after=0"),
    ("GET", "/v1/preflight"),
    ("POST", "/v1/health"),
    ("GET", "/v1/health/"),
    ("GET", "/v1/unknown"),
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    ("GET", "/"),
]
WRONG_AUTHORIZATIONS = [
    pytest.param(None, id="missing"),
    pytest.param("", id="empty"),
    pytest.param("Bearer", id="scheme-only"),
    pytest.param("Bearer ", id="empty-token"),
    pytest.param("Bearer " + "cd" * 32, id="wrong-token"),
    pytest.param("Bearer " + TOKEN[:-1], id="prefix"),
    pytest.param("Bearer " + TOKEN + "0", id="longer"),
    pytest.param("Bearer " + TOKEN.upper(), id="other-case"),
    pytest.param("Basic " + TOKEN, id="other-scheme"),
    pytest.param(TOKEN, id="no-scheme"),
    pytest.param(("Bearer " + TOKEN[:-2]).encode() + "é".encode(), id="non-ascii"),
]


class FakeProbe:
    def read_facts(self) -> GpuFacts:
        return GpuFacts("NVIDIA GeForce RTX 4070", "596.36", CudaVersion(13, 0), self.read_memory())

    def read_memory(self) -> VramReading:
        return VramReading(total_mib=12282, used_mib=1489, free_mib=10793)

    def close(self) -> None:
        pass


@pytest.fixture
def app() -> FastAPI:
    return create_app(TOKEN, GPUResourceMonitor(FakeProbe()))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def headers(authorization: str | bytes | None) -> dict[str, str | bytes]:
    return {} if authorization is None else {"Authorization": authorization}


@pytest.mark.parametrize("authorization", WRONG_AUTHORIZATIONS)
@pytest.mark.parametrize(("method", "path"), GUARDED_ROUTES)
def test_every_route_but_health_refuses_a_missing_or_wrong_token(
    client: TestClient, method: str, path: str, authorization: str | bytes | None
) -> None:
    response = client.request(method, path, headers=headers(authorization), json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("authorization", [*WRONG_AUTHORIZATIONS, pytest.param("Bearer " + TOKEN, id="right")])
def test_health_answers_without_a_token(client: TestClient, authorization: str | bytes | None) -> None:
    response = client.get("/v1/health", headers=headers(authorization))
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


@pytest.mark.parametrize("authorization", ["Bearer " + TOKEN, "bearer  " + TOKEN + " ", "BEARER " + TOKEN])
def test_the_right_token_reaches_the_preflight(client: TestClient, authorization: str) -> None:
    response = client.get("/v1/preflight", headers={"Authorization": authorization})
    assert response.status_code == 200
    report = response.json()
    assert report["ok"] is True
    assert [row["check"] for row in report["checks"]] == [
        "gpu_in_container",
        "cuda_driver_version",
        "total_vram",
        "free_vram",
    ]


def test_the_right_token_on_an_undelivered_route_gets_404_and_no_schema_is_served(client: TestClient) -> None:
    authorized = {"Authorization": "Bearer " + TOKEN}
    assert client.get("/v1/state", headers=authorized).status_code == 404
    assert client.get("/openapi.json", headers=authorized).status_code == 404
    assert client.get("/docs", headers=authorized).status_code == 404


def test_tokens_are_compared_in_constant_time(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    compared: list[tuple[bytes, bytes]] = []

    def spy(presented: bytes, expected: bytes) -> bool:
        compared.append((presented, expected))
        return False

    # api.py calls hmac.compare_digest through the module, so replacing the module's attribute reaches it.
    monkeypatch.setattr(hmac, "compare_digest", spy)
    assert client.get("/v1/preflight", headers={"Authorization": "Bearer " + "cd" * 32}).status_code == 401
    assert client.get("/v1/preflight").status_code == 401
    assert compared == [(("cd" * 32).encode(), TOKEN.encode()), (b"", TOKEN.encode())]


@pytest.mark.parametrize("token", ["", "generated-by-setup", "ab" * 31, "zz" * 32, "ab" * 33])
def test_an_unusable_token_is_refused_without_its_value(token: str) -> None:
    with pytest.raises(ValueError, match="64 hexadecimal characters") as raised:
        create_app(token, GPUResourceMonitor(FakeProbe()))
    assert not token or token not in str(raised.value)
