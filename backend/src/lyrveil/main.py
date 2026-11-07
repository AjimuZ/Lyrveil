"""Composition root: loads settings, opens and migrates the database, builds the FastAPI application and serves it."""

import sys
import uuid
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from lyrveil import __version__
from lyrveil.api import events, health
from lyrveil.api.security import (
    LocalOnlyMiddleware,
    SecurityHeadersMiddleware,
    documented_headers,
    internal_error_handler,
)
from lyrveil.logs import configure_logging, get_logger
from lyrveil.orchestration.health import probe_control_api
from lyrveil.persistence.database import (
    DEFAULT_DATABASE_PATH,
    DatabaseCapabilityError,
    create_database_engine,
    verify_capabilities,
)
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings, SettingsError, load_settings

API_PREFIX = "/api/v1"
LISTEN_HOST = "0.0.0.0"  # Inside the container only; Compose publishes no backend port.
LISTEN_PORT = 8000
# The Host of the container's own health check, which connects to the loopback of the backend container. A browser
# can never send it: the backend publishes no port, and nginx passes on the browser's Host, which names the app's port.
INTERNAL_HOST = f"127.0.0.1:{LISTEN_PORT}"
# EX_CONFIG from sysexits.h, the status the runtime entrypoints use for a configuration error.
EXIT_INVALID_SETTINGS = 78
# EX_UNAVAILABLE: the database is missing WAL or FTS5, or cannot be opened or migrated.
EXIT_DATABASE_UNAVAILABLE = 69
DATABASE_PATH = DEFAULT_DATABASE_PATH


def create_app(
    settings: Settings,
    engine: Engine | None = None,
    control_probe: Callable[[], bool] = probe_control_api,
) -> FastAPI:
    """Returns the FastAPI application for the given settings, which it keeps in `app.state.settings`.

    `engine` is the verified and migrated database, kept in `app.state.engine`; without it `/health/ready` answers
    503. `control_probe` tells whether the control API answers and may be replaced in tests. The OpenAPI document is
    served at /api/v1/openapi.json. The interactive documentation pages are disabled, because they load scripts from
    a CDN and the application works offline.

    Every request, REST or WebSocket, must name an allowed Host, and a write or a handshake an allowed Origin, or it
    gets 403 `FORBIDDEN_ORIGIN` (docs/security.md). Every answer carries the documented security headers, and an
    unexpected exception becomes a generic 500 `INTERNAL`.
    """
    app = FastAPI(
        title="Lyrveil",
        version=__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.control_probe = control_probe
    app.state.boot_id = str(uuid.uuid4())
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(events.router)
    headers = documented_headers(settings.http_port)
    app.add_exception_handler(Exception, internal_error_handler(headers))
    # The middleware added last runs first: the headers wrap the Host and Origin refusals too.
    app.add_middleware(
        LocalOnlyMiddleware,
        hosts=(*settings.allowed_hosts, INTERNAL_HOST),
        origins=settings.allowed_origins,
    )
    app.add_middleware(SecurityHeadersMiddleware, headers=headers)
    return app


def main() -> int:
    """Starts the backend and returns the process exit status.

    Returns 78 with a message on stderr naming each invalid setting, before logging or the server starts. Then opens
    the database, verifies WAL and FTS5, and applies the migrations; if that fails it logs `DB_CAPABILITY_MISSING`
    or `database_unavailable` and returns 69, so the backend never becomes ready on a database it cannot trust.
    Otherwise serves on port 8000 with a single worker, which the in-process WebSocket hub and job worker require,
    until the server stops, and returns 0. The access log is off, because request paths and queries can carry user
    text.
    """
    try:
        settings = load_settings()
    except SettingsError as error:
        print(f"lyrveil: {error}", file=sys.stderr)
        return EXIT_INVALID_SETTINGS
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    logger.info("backend_starting")
    try:
        engine = create_database_engine(DATABASE_PATH)
        verify_capabilities(engine)
        upgrade_to_head(engine)
    except DatabaseCapabilityError as error:
        logger.error("database_capability_missing", error_code=error.code)
        return EXIT_DATABASE_UNAVAILABLE
    except (SQLAlchemyError, OSError):
        logger.error("database_unavailable", exc_info=True)
        return EXIT_DATABASE_UNAVAILABLE
    uvicorn.run(
        create_app(settings, engine),
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        log_config=None,
        access_log=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
