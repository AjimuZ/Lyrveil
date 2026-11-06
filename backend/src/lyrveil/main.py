"""Composition root: loads the settings, configures logging, builds the FastAPI application and serves it."""

import sys

import uvicorn
from fastapi import FastAPI

from lyrveil import __version__
from lyrveil.logs import configure_logging, get_logger
from lyrveil.settings import Settings, SettingsError, load_settings

API_PREFIX = "/api/v1"
LISTEN_HOST = "0.0.0.0"  # Inside the container only; Compose publishes no backend port.
LISTEN_PORT = 8000
# EX_CONFIG from sysexits.h, the status the runtime entrypoints use for a configuration error.
EXIT_INVALID_SETTINGS = 78


def create_app(settings: Settings) -> FastAPI:
    """Returns the FastAPI application for the given settings, which it keeps in `app.state.settings`.

    The OpenAPI document is served at /api/v1/openapi.json. The interactive documentation pages are disabled,
    because they load scripts from a CDN and the application works offline.
    """
    app = FastAPI(
        title="Lyrveil",
        version=__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    return app


def main() -> int:
    """Starts the backend and returns the process exit status.

    Returns 78 with a message on stderr naming each invalid setting, before logging or the server starts. Otherwise
    serves on port 8000 with a single worker, which the in-process WebSocket hub and job worker require, until the
    server stops, and returns 0. The access log is off, because request paths and queries can carry user text.
    """
    try:
        settings = load_settings()
    except SettingsError as error:
        print(f"lyrveil: {error}", file=sys.stderr)
        return EXIT_INVALID_SETTINGS
    configure_logging(settings.log_level)
    get_logger(__name__).info("backend_starting")
    uvicorn.run(
        create_app(settings),
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        log_config=None,
        access_log=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
