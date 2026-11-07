"""Composition root: loads settings, opens and migrates the database, builds the FastAPI application and serves it."""

import sys
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

import anyio
import uvicorn
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from lyrveil import __version__
from lyrveil.api import conversations, events, health, messages
from lyrveil.api.security import (
    LocalOnlyMiddleware,
    SecurityHeadersMiddleware,
    documented_headers,
    domain_error_handler,
    internal_error_handler,
)
from lyrveil.domain.errors import DomainError
from lyrveil.events.hub import EventHub
from lyrveil.jobs.policy import JobKind, RequiredMode
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import JobScheduler, LeaseProvider, recover_interrupted_jobs
from lyrveil.logs import configure_logging, get_logger
from lyrveil.orchestration.client import OrchestratorClient
from lyrveil.orchestration.health import probe_control_api
from lyrveil.persistence.database import (
    DEFAULT_DATABASE_PATH,
    DatabaseCapabilityError,
    create_database_engine,
    verify_capabilities,
)
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import TextRuntime
from lyrveil.runtimes.koboldcpp import KoboldCppRuntime
from lyrveil.services.chat_inference import ChatInferenceService, ReplySettings, recover_interrupted_replies
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


def _llm_mode() -> RequiredMode:
    # P02 serves text only, and the backend does not follow the GPU state yet (startup step 6 is planned): it
    # assumes the llm mode, and a refused lease (NOT_READY) keeps a reply queued until the runtime is ready.
    return RequiredMode.LLM


@dataclass(frozen=True)
class Worker:
    """What the job worker takes from outside the application: a factory of the text runtime, opened for the
    application's life, the lease calls, the GPU mode whose jobs may run, and the reply settings.

    Tests pass fakes.
    """

    text_runtime: Callable[[], AbstractAsyncContextManager[TextRuntime]]
    leases: LeaseProvider
    current_mode: Callable[[], RequiredMode] = _llm_mode
    replies: ReplySettings = field(default_factory=ReplySettings)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs the job worker for the life of the application (startup step 7), when it has a database and a Worker.

    The worker runs `chat_reply` and `chat_regenerate` with ChatInferenceService and is kept in
    `app.state.scheduler` meanwhile. On shutdown the running reply keeps its flushed text and stays `streaming`
    for the next start to mark `interrupted`; its lease is not released and expires by itself.
    """
    worker: Worker | None = app.state.worker
    engine: Engine | None = app.state.engine
    if worker is None or engine is None:
        yield
        return
    async with worker.text_runtime() as runtime:
        replies = ChatInferenceService(engine, runtime, app.state.events, worker.replies)
        handlers = {JobKind.CHAT_REPLY: replies, JobKind.CHAT_REGENERATE: replies}
        scheduler = JobScheduler(engine, JobQueue(), handlers, worker.leases, worker.current_mode)
        async with anyio.create_task_group() as group:
            group.start_soon(scheduler.run_forever)
            app.state.scheduler = scheduler
            try:
                yield
            finally:
                app.state.scheduler = None
                replies.close()
                group.cancel_scope.cancel()


def create_app(
    settings: Settings,
    engine: Engine | None = None,
    control_probe: Callable[[], bool] = probe_control_api,
    worker: Worker | None = None,
) -> FastAPI:
    """Returns the FastAPI application for the given settings, which it keeps in `app.state.settings`.

    `engine` is the verified and migrated database, kept in `app.state.engine`; without it `/health/ready` answers
    503. `control_probe` tells whether the control API answers and may be replaced in tests. With `worker` and an
    `engine`, the application's lifespan runs the job worker; without them queued jobs wait. `app.state.events` is
    the EventHub that `/ws` serves. The OpenAPI document is served at /api/v1/openapi.json. The interactive
    documentation pages are disabled, because they load scripts from a CDN and the application works offline.

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
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.control_probe = control_probe
    app.state.boot_id = str(uuid.uuid4())
    app.state.events = EventHub(app.state.boot_id)
    app.state.worker = worker
    app.state.scheduler = None
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(conversations.router, prefix=API_PREFIX)
    app.include_router(messages.router, prefix=API_PREFIX)
    app.include_router(events.router)
    headers = documented_headers(settings.http_port)
    app.add_exception_handler(DomainError, domain_error_handler)
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
    the database, verifies WAL and FTS5, applies the migrations, marks the replies a restart cut `interrupted`
    (recover_interrupted_replies), and requeues or fails the other jobs it interrupted (JobQueue.recover_interrupted);
    if that fails it logs `DB_CAPABILITY_MISSING` or `database_unavailable` and returns 69, so the backend never
    becomes ready on a database it cannot trust. Otherwise serves on port 8000 with a single worker, which the
    in-process WebSocket hub and job worker require, until the server stops, and returns 0. The job worker streams
    replies from KoboldCpp under leases of the control API. The access log is off, because request paths and queries
    can carry user text.
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
        # Startup step 5 (docs/backend.md): no worker runs yet, so every `running` job was interrupted by a restart.
        # The replies go first, because they end their own jobs instead of queuing them again.
        queue = JobQueue()
        recover_interrupted_replies(engine, queue)
        recover_interrupted_jobs(engine, queue)
    except DatabaseCapabilityError as error:
        logger.error("database_capability_missing", error_code=error.code)
        return EXIT_DATABASE_UNAVAILABLE
    except (SQLAlchemyError, OSError):
        logger.error("database_unavailable", exc_info=True)
        return EXIT_DATABASE_UNAVAILABLE
    worker = Worker(text_runtime=KoboldCppRuntime, leases=OrchestratorClient(settings.control_token))
    uvicorn.run(
        create_app(settings, engine, worker=worker),
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        log_config=None,
        access_log=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
