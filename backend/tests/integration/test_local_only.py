"""T-SEC-01 and the backend half of T-SEC-02: Host and Origin checks on REST and WebSocket, and security headers."""

from collections.abc import Mapping

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.testclient import WebSocketDenialResponse

from lyrveil.main import create_app
from lyrveil.settings import Settings

APP_URL = "http://localhost:4200"
# websocket_connect resolves a relative path against ws://testserver, whatever base_url says, so sockets use full URLs.
APP_SOCKET = "ws://localhost:4200/ws"
LIVE = "/api/v1/health/live"
FOREIGN_ORIGIN = "http://evil.example"
DOCUMENTED = {
    "content-security-policy": (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; "
        "font-src 'self'; connect-src 'self' ws://localhost:4200 ws://127.0.0.1:4200; manifest-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings, engine=None, control_probe=lambda: False)


def assert_documented_headers(headers: Mapping[str, str]) -> None:
    assert {name: headers.get(name) for name in DOCUMENTED} == DOCUMENTED
    # No CORS: another origin can never read an answer.
    assert headers.get("access-control-allow-origin") is None


def assert_forbidden_origin(status: int, content_type: str | None, body: dict[str, object]) -> None:
    assert status == 403
    assert content_type == "application/problem+json"
    assert body["code"] == "FORBIDDEN_ORIGIN"
    assert body["status"] == 403


@pytest.mark.parametrize("url", ["http://localhost:4200", "http://127.0.0.1:4200", "http://LOCALHOST:4200"])
def test_the_local_hosts_reach_the_api(app: FastAPI, url: str) -> None:
    assert TestClient(app, base_url=url).get(LIVE).status_code == 200


def test_the_health_check_of_the_container_is_accepted(app: FastAPI) -> None:
    assert TestClient(app, base_url="http://127.0.0.1:8000").get(LIVE).status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example:4200",  # DNS rebinding: the page's own name, resolved to 127.0.0.1
        "http://localhost:4201",
        "http://localhost",
        "http://192.168.1.10:4200",  # LAN exposure is unsupported
        "http://localhost:8000",
        "http://testserver",
    ],
)
def test_a_wrong_host_is_refused_on_rest(app: FastAPI, url: str) -> None:
    response = TestClient(app, base_url=url).get(LIVE)
    assert_forbidden_origin(response.status_code, response.headers.get("content-type"), response.json())
    assert_documented_headers(response.headers)


@pytest.mark.parametrize("url", ["ws://evil.example:4200/ws", "ws://localhost:4201/ws", "ws://testserver/ws"])
def test_a_wrong_host_is_refused_on_the_websocket_handshake(app: FastAPI, url: str) -> None:
    client = TestClient(app)
    with pytest.raises(WebSocketDenialResponse) as refused, client.websocket_connect(url):
        pass
    answer = refused.value
    assert_forbidden_origin(answer.status_code, answer.headers.get("content-type"), answer.json())
    assert_documented_headers(answer.headers)


@pytest.mark.parametrize("origin", [FOREIGN_ORIGIN, "null", "http://localhost:4201", "https://localhost:4200"])
def test_a_wrong_origin_is_refused_on_a_rest_write(app: FastAPI, origin: str) -> None:
    response = TestClient(app, base_url=APP_URL).post(LIVE, headers={"Origin": origin}, json={})
    assert_forbidden_origin(response.status_code, response.headers.get("content-type"), response.json())
    assert_documented_headers(response.headers)


@pytest.mark.parametrize("origin", [FOREIGN_ORIGIN, "null"])
def test_a_wrong_origin_is_refused_on_the_websocket_handshake(app: FastAPI, origin: str) -> None:
    client = TestClient(app)
    headers = {"Origin": origin}
    with pytest.raises(WebSocketDenialResponse) as refused, client.websocket_connect(APP_SOCKET, headers=headers):
        pass
    answer = refused.value
    assert_forbidden_origin(answer.status_code, answer.headers.get("content-type"), answer.json())


def test_a_write_from_the_app_passes_the_checks(app: FastAPI) -> None:
    # The route has no POST, so 405 shows the request went past both checks to the router.
    response = TestClient(app, base_url=APP_URL).post(LIVE, headers={"Origin": "http://127.0.0.1:4200"}, json={})
    assert response.status_code == 405


def test_a_read_needs_no_origin_check(app: FastAPI) -> None:
    # Without CORS headers the page that sent it cannot read the answer.
    response = TestClient(app, base_url=APP_URL).get(LIVE, headers={"Origin": FOREIGN_ORIGIN})
    assert response.status_code == 200
    assert_documented_headers(response.headers)


@pytest.mark.parametrize("origin", ["http://localhost:4200", "http://127.0.0.1:4200", None])
def test_the_app_opens_the_websocket_and_gets_the_connected_frame(app: FastAPI, origin: str | None) -> None:
    headers = {} if origin is None else {"Origin": origin}
    with TestClient(app).websocket_connect(APP_SOCKET, headers=headers) as socket:
        frame = socket.receive_json()
        socket.send_json({"type": "ping"})
    assert frame == {"v": 1, "type": "connected", "boot_id": app.state.boot_id}


def test_the_allowed_hosts_follow_the_configured_port() -> None:
    settings = Settings(control_token=SecretStr("ab" * 32), http_port=14200)
    app = create_app(settings, engine=None, control_probe=lambda: False)
    assert TestClient(app, base_url="http://localhost:14200").get(LIVE).status_code == 200
    assert TestClient(app, base_url=APP_URL).get(LIVE).status_code == 403
    write = TestClient(app, base_url="http://localhost:14200").post(LIVE, headers={"Origin": APP_URL})
    assert write.status_code == 403
    csp = TestClient(app, base_url="http://localhost:14200").get(LIVE).headers["content-security-policy"]
    assert "connect-src 'self' ws://localhost:14200 ws://127.0.0.1:14200;" in csp


def test_api_errors_carry_the_headers(app: FastAPI) -> None:
    response = TestClient(app, base_url=APP_URL).get("/api/v1/no-such-route")
    assert response.status_code == 404
    assert_documented_headers(response.headers)


def test_an_unexpected_failure_is_a_generic_500_with_the_headers(app: FastAPI) -> None:
    def fail() -> None:
        raise RuntimeError("private text that must not reach the answer")

    app.add_api_route("/api/v1/fail", fail)
    response = TestClient(app, base_url=APP_URL, raise_server_exceptions=False).get("/api/v1/fail")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "INTERNAL"
    assert "private text" not in response.text
    assert_documented_headers(response.headers)
