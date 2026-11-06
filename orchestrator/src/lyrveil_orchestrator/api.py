"""The control API of docs/api.md (Control API): the backend's only way into the orchestrator, behind a bearer token.

Every request except `GET /v1/health` must carry `Authorization: Bearer <LYRVEIL_CONTROL_TOKEN>`. The check runs as
middleware before routing, so it covers every path, including routes later phases add and paths that do not exist: a
request without the right token learns nothing about which routes are there. The presented token is compared with
hmac.compare_digest, in time that does not depend on where the two first differ.

Routes delivered so far: `/v1/health` and `/v1/preflight`. The state, desired mode, lease, recovery, drain and event
routes come with the lifecycle manager; until then they answer 404 to an authorized request.
"""

import hmac
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from lyrveil_orchestrator import __version__
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor
from lyrveil_orchestrator.preflight import run_preflight

HEALTH_PATH = "/v1/health"
# setup.cmd writes 32 random bytes as hex, the format the backend also requires (docs/configuration.md).
_HEX_TOKEN = re.compile(r"[0-9a-fA-F]{64}")
_BEARER = "bearer"
_PROBLEM_JSON = "application/problem+json"


def _unauthorized() -> JSONResponse:
    # The same answer for a missing header, another scheme and a wrong token, so the answer tells nothing apart.
    return JSONResponse(
        {
            "type": "https://lyrveil.invalid/errors/unauthorized",
            "title": "Unauthorized",
            "status": 401,
            "code": "UNAUTHORIZED",
        },
        status_code=401,
        media_type=_PROBLEM_JSON,
        headers={"WWW-Authenticate": "Bearer"},
    )


def presented_token(authorization: str | None) -> bytes:
    """Returns the token of an `Authorization: Bearer <token>` header as bytes, or b"" for anything else.

    The scheme is case-insensitive (RFC 6750). Starlette decodes header values as Latin-1, so encoding them back as
    Latin-1 gives the bytes on the wire and never fails; compare_digest needs bytes for text outside ASCII.
    """
    if authorization is None:
        return b""
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != _BEARER:
        return b""
    return token.strip().encode("latin-1")


def create_app(control_token: str, monitor: GPUResourceMonitor) -> FastAPI:
    """Returns the control API application, which requires `control_token` on every route but HEALTH_PATH.

    Raises ValueError, without the value, unless `control_token` is 64 hexadecimal characters: an empty or placeholder
    token would make the check meaningless. `monitor` serves `/v1/preflight`; the caller keeps ownership of it.
    The OpenAPI document and its pages are off: the API is internal and its contract is docs/api.md.
    """
    if not _HEX_TOKEN.fullmatch(control_token):
        raise ValueError("The control token must be 64 hexadecimal characters, as written by setup.cmd")
    expected = control_token.encode("ascii")

    app = FastAPI(
        title="Lyrveil control API",
        version=__version__,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def require_token(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # Only the documented GET is open: another method on the same path needs the token like any other route.
        if request.method == "GET" and request.url.path == HEALTH_PATH:
            return await call_next(request)
        if not hmac.compare_digest(presented_token(request.headers.get("authorization")), expected):
            return _unauthorized()
        return await call_next(request)

    @app.get(HEALTH_PATH)
    def health() -> dict[str, str]:
        """Answers 200 whenever the process serves requests. Needs no token, so health checks can call it."""
        return {"status": "alive"}

    @app.get("/v1/preflight")
    def preflight() -> dict[str, Any]:
        """Answers 200 with the GPU rows of the preflight; a failed row is in the body, not in the status code.

        A plain def, so FastAPI runs the blocking NVML reads in its thread pool.
        """
        return run_preflight(monitor).to_dict()

    return app
