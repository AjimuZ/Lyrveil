"""Tests the control API probe against a local HTTP server."""

from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from lyrveil.orchestration.health import probe_control_api


class Handler(BaseHTTPRequestHandler):
    status = 200

    def do_GET(self) -> None:
        self.send_response(self.status if self.path == "/v1/health" else 404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Keeps the test output quiet."""


@pytest.fixture
def server() -> Iterator[HTTPServer]:
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def url(httpd: HTTPServer) -> str:
    return f"http://127.0.0.1:{httpd.server_port}"


def test_a_200_from_the_health_route_is_reachable(server: HTTPServer) -> None:
    assert probe_control_api(url(server)) is True


def test_any_other_answer_is_not_reachable(server: HTTPServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Handler, "status", 503)
    assert probe_control_api(url(server)) is False


def test_a_closed_port_is_not_reachable_and_does_not_raise(server: HTTPServer) -> None:
    base = url(server)
    server.shutdown()
    server.server_close()
    assert probe_control_api(base, timeout_s=0.5) is False
