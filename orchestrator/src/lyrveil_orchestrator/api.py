"""The control API of docs/api.md (Control API): the backend's only way into the orchestrator, behind a bearer token.

Every request except `GET /v1/health` must carry `Authorization: Bearer <LYRVEIL_CONTROL_TOKEN>`. The check runs as
middleware before routing, so it covers every path, including routes later phases add and paths that do not exist: a
request without the right token learns nothing about which routes are there. The presented token is compared with
hmac.compare_digest, in time that does not depend on where the two first differ.

Every route of the table in docs/api.md is delivered. The lifecycle routes delegate to ModelLifecycleManager, whose
run loop the application starts with its lifespan and cancels on shutdown. Requests carry only enums, identifiers and
numbers: a job id is 1 to 64 letters, digits, `-` or `_`.
"""

import asyncio
import hmac
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from lyrveil_orchestrator import __version__
from lyrveil_orchestrator.gpu_monitor import GPUResourceMonitor
from lyrveil_orchestrator.lifecycle import (
    DEFAULT_LEASE_TTL_S,
    MAX_LEASE_TTL_S,
    LeaseExpiredError,
    LeaseOutcome,
    LeaseRefusedError,
    LifecycleConflictError,
    Mode,
    ModelLifecycleManager,
)
from lyrveil_orchestrator.preflight import run_preflight

HEALTH_PATH = "/v1/health"
# The longest long poll of GET /v1/state (docs/api.md).
MAX_STATE_WAIT_S = 25.0
# setup.cmd writes 32 random bytes as hex, the format the backend also requires (docs/configuration.md).
_HEX_TOKEN = re.compile(r"[0-9a-fA-F]{64}")
_BEARER = "bearer"
_PROBLEM_JSON = "application/problem+json"
_JOB_ID = r"^[A-Za-z0-9_-]{1,64}$"

JobId = Annotated[str, Field(pattern=_JOB_ID)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DesiredModeRequest(_Strict):
    mode: Mode
    job_id: JobId | None = None


class LeaseRequest(_Strict):
    mode: Literal["llm", "image"]
    job_id: JobId
    ttl_s: int = Field(default=DEFAULT_LEASE_TTL_S, ge=1, le=MAX_LEASE_TTL_S)


class LeaseRelease(_Strict):
    outcome: LeaseOutcome


def _problem(status: int, code: str, title: str, headers: dict[str, str] | None = None) -> JSONResponse:
    """Returns an RFC 9457 problem with `code`, the form every refusal of the control API takes."""
    return JSONResponse(
        {
            "type": f"https://lyrveil.invalid/errors/{code.lower().replace('_', '-')}",
            "title": title,
            "status": status,
            "code": code,
        },
        status_code=status,
        media_type=_PROBLEM_JSON,
        headers=headers,
    )


def _unauthorized() -> JSONResponse:
    # The same answer for a missing header, another scheme and a wrong token, so the answer tells nothing apart.
    return _problem(401, "UNAUTHORIZED", "Unauthorized", headers={"WWW-Authenticate": "Bearer"})


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


def create_app(control_token: str, monitor: GPUResourceMonitor, lifecycle: ModelLifecycleManager) -> FastAPI:
    """Returns the control API application, which requires `control_token` on every route but HEALTH_PATH.

    Raises ValueError, without the value, unless `control_token` is 64 hexadecimal characters: an empty or placeholder
    token would make the check meaningless. `monitor` serves `/v1/preflight` and `lifecycle` every other route; the
    caller keeps ownership of both. The lifespan runs `lifecycle.run()` while the application serves.
    The OpenAPI document and its pages are off: the API is internal and its contract is docs/api.md.
    """
    if not _HEX_TOKEN.fullmatch(control_token):
        raise ValueError("The control token must be 64 hexadecimal characters, as written by setup.cmd")
    expected = control_token.encode("ascii")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(lifecycle.run())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="Lyrveil control API",
        version=__version__,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
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

    @app.get("/v1/state")
    async def state(
        since: Annotated[int | None, Query(ge=0)] = None,
        wait: Annotated[float, Query(ge=0, le=MAX_STATE_WAIT_S)] = 0.0,
    ) -> dict[str, Any]:
        """Answers the state document; with `since`, first waits up to `wait` seconds for `seq` to exceed it."""
        if since is not None and wait > 0:
            await lifecycle.wait_for_change(since, wait)
        return await lifecycle.snapshot()

    @app.put("/v1/desired-mode", status_code=202, response_model=None)
    async def desired_mode(body: DesiredModeRequest) -> dict[str, Any] | JSONResponse:
        """Records the desired mode, applied once no lease is active, on behalf of the body's `job_id`, which the
        events of the switch then carry; 409 CONFLICT while draining.

        This route and the other lifecycle routes are async on purpose: the manager is not thread-safe, so it is only
        touched from the event loop, never from FastAPI's thread pool.
        """
        try:
            lifecycle.request_mode(body.mode, body.job_id)
        except LifecycleConflictError:
            return _problem(409, "CONFLICT", "The orchestrator is draining")
        return {"desired_mode": body.mode.value, "state": lifecycle.state.value}

    @app.post("/v1/leases", status_code=201, response_model=None)
    async def acquire_lease(body: LeaseRequest) -> dict[str, Any] | JSONResponse:
        """Grants the lease: 201, or 409 with NOT_READY or LEASE_HELD."""
        try:
            lease = lifecycle.acquire(Mode(body.mode), body.job_id, body.ttl_s)
        except LeaseRefusedError as refusal:
            return _problem(409, refusal.code, "Lease refused")
        return lifecycle.lease_document(lease)

    @app.post("/v1/leases/{lease_id}/heartbeat", response_model=None)
    async def heartbeat(lease_id: str) -> dict[str, Any] | JSONResponse:
        """Extends the lease: 200, or 410 LEASE_EXPIRED when it is no longer the current lease."""
        try:
            lease = lifecycle.heartbeat(lease_id)
        except LeaseExpiredError:
            return _problem(410, "LEASE_EXPIRED", "Lease expired")
        return lifecycle.lease_document(lease)

    @app.delete("/v1/leases/{lease_id}", response_model=None)
    async def release_lease(lease_id: str, body: LeaseRelease) -> dict[str, Any] | JSONResponse:
        """Ends the lease with its outcome: 200, or 410 LEASE_EXPIRED when it is no longer the current lease."""
        try:
            lifecycle.release(lease_id, body.outcome)
        except LeaseExpiredError:
            return _problem(410, "LEASE_EXPIRED", "Lease expired")
        return {"released": True, "state": lifecycle.state.value}

    @app.post("/v1/recover", status_code=202, response_model=None)
    async def recover() -> dict[str, Any] | JSONResponse:
        """Leaves ERROR for RECOVERING: 202, or 409 CONFLICT in any other state."""
        try:
            lifecycle.recover()
        except LifecycleConflictError:
            return _problem(409, "CONFLICT", "The state is not ERROR")
        return {"state": lifecycle.state.value}

    @app.post("/v1/drain", status_code=202)
    async def drain() -> dict[str, Any]:
        """Starts the drain: the runtime stops once the current lease ends. Poll the state for IDLE."""
        lifecycle.drain()
        return {"draining": True, "state": lifecycle.state.value}

    @app.post("/v1/resume", status_code=202)
    async def resume() -> dict[str, Any]:
        """Leaves the drain."""
        lifecycle.resume()
        return {"draining": False, "state": lifecycle.state.value}

    @app.get("/v1/events")
    async def events(boot_id: str | None = None, after: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
        """Answers the buffered transitions after `after`, all of them when `boot_id` is not this process's."""
        return {"boot_id": lifecycle.boot_id, "events": lifecycle.events(boot_id, after)}

    return app
