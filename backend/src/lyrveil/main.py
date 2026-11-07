"""Composition root: loads settings, opens and migrates the database, builds the FastAPI application and serves it."""

import sys
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import uvicorn
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from lyrveil import __version__
from lyrveil.api import (
    assets,
    characters,
    conversations,
    diagnostics,
    events,
    health,
    images,
    jobs,
    media,
    memories,
    messages,
    scenarios,
)
from lyrveil.api.security import (
    LocalOnlyMiddleware,
    SecurityHeadersMiddleware,
    documented_headers,
    domain_error_handler,
    internal_error_handler,
)
from lyrveil.context.builder import ContextSettings
from lyrveil.domain.errors import DomainError
from lyrveil.events.hub import EventHub
from lyrveil.jobs.handlers import JobHandler
from lyrveil.jobs.policy import JobKind, RequiredMode
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import JobScheduler, LeaseProvider, ModeSwitch, recover_interrupted_jobs
from lyrveil.logs import configure_logging, get_logger
from lyrveil.media.store import DEFAULT_MEDIA_ROOT, MediaStore
from lyrveil.memory.summaries import SummarySettings
from lyrveil.memory.window import ExtractionSettings
from lyrveil.orchestration.client import OrchestratorClient
from lyrveil.orchestration.health import probe_control_api
from lyrveil.persistence.database import (
    DEFAULT_DATABASE_PATH,
    DatabaseCapabilityError,
    create_database_engine,
    verify_capabilities,
)
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.image_runtime import ImageRuntime
from lyrveil.ports.text_runtime import TextRuntime
from lyrveil.runtimes.comfyui import ComfyUiRuntime
from lyrveil.runtimes.koboldcpp import KoboldCppRuntime
from lyrveil.services.character_creator import CharacterCreatorService
from lyrveil.services.chat_inference import ChatInferenceService, ReplySettings, recover_interrupted_replies
from lyrveil.services.daily_backup import DailyBackup
from lyrveil.services.image_generation import ImageGenerationService, ImageSettings
from lyrveil.services.memory_extraction import MemoryExtractionService
from lyrveil.services.summaries import SummaryService
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
MEDIA_ROOT = DEFAULT_MEDIA_ROOT
# The data volume, which holds db/, media/, runtime/ and the daily backups in backups/ (docs/operations.md).
DATA_ROOT = Path("/data")


def _llm_mode() -> RequiredMode:
    # Without a mode switch the worker does not follow the GPU state: it assumes the llm mode, and a refused lease
    # (NOT_READY) keeps a reply queued until the runtime is ready.
    return RequiredMode.LLM


@dataclass(frozen=True)
class Worker:
    """What the job worker takes from outside the application: a factory of the text runtime, opened for the
    application's life, the lease calls, the GPU mode whose jobs may run, the reply settings, the memory extraction,
    summary and prompt budget settings, and optionally a factory of the image runtime with the image settings and the
    mode calls of the orchestrator.

    Without `image_runtime` the worker has no `image_request` handler and such jobs wait in the queue. With
    `mode_switch`, the scheduler reads the GPU mode from the orchestrator and asks for the mode its queue needs,
    instead of calling `current_mode` (JobScheduler). `main` passes ComfyUiRuntime and the OrchestratorClient for
    both; tests pass fakes.
    """

    text_runtime: Callable[[], AbstractAsyncContextManager[TextRuntime]]
    leases: LeaseProvider
    current_mode: Callable[[], RequiredMode] = _llm_mode
    mode_switch: ModeSwitch | None = None
    image_runtime: Callable[[], AbstractAsyncContextManager[ImageRuntime]] | None = None
    images: ImageSettings = field(default_factory=ImageSettings)
    replies: ReplySettings = field(default_factory=ReplySettings)
    memory: ExtractionSettings = field(default_factory=ExtractionSettings)
    summaries: SummarySettings = field(default_factory=SummarySettings)
    context: ContextSettings = field(default_factory=ContextSettings)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs the daily backup, when `app.state.daily_backup` holds one, and the job worker for the life of the
    application. On shutdown a backup being written is finished before the application ends."""
    daily_backup: DailyBackup | None = app.state.daily_backup
    async with anyio.create_task_group() as group:
        if daily_backup is not None:
            group.start_soon(daily_backup.run_forever)
        try:
            async with _job_worker(app):
                yield
        finally:
            group.cancel_scope.cancel()


@asynccontextmanager
async def _job_worker(app: FastAPI) -> AsyncIterator[None]:
    """Runs the job worker while the context is open (startup step 7), when the app has a database and a Worker.

    The worker runs `chat_reply` and `chat_regenerate` with ChatInferenceService, `character_draft` with
    CharacterCreatorService, `memory_extraction` and `memory_reevaluation` with MemoryExtractionService,
    `summary_checkpoint` with SummaryService, `image_request` with ImageGenerationService when the Worker has an
    image runtime, and is kept in `app.state.scheduler` meanwhile. On shutdown the running reply keeps its flushed
    text and stays `streaming` for the next start to mark `interrupted`; its lease is not released and expires by
    itself.
    """
    worker: Worker | None = app.state.worker
    engine: Engine | None = app.state.engine
    if worker is None or engine is None:
        yield
        return
    async with AsyncExitStack() as stack:
        runtime = await stack.enter_async_context(worker.text_runtime())
        image_runtime: ImageRuntime | None = None
        if worker.image_runtime is not None:
            image_runtime = await stack.enter_async_context(worker.image_runtime())
        replies = ChatInferenceService(
            engine,
            runtime,
            app.state.events,
            worker.replies,
            extraction=worker.memory,
            summaries=worker.summaries,
            context=worker.context,
        )
        drafts = CharacterCreatorService(engine, runtime, template=worker.replies.template)
        memories = MemoryExtractionService(
            engine,
            runtime,
            template=worker.replies.template,
            user=worker.replies.user,
            settings=worker.memory,
            events=app.state.events,
            zone=app.state.settings.zone,
        )
        summaries = SummaryService(
            engine,
            runtime,
            template=worker.replies.template,
            user=worker.replies.user,
            settings=worker.summaries,
        )
        handlers: dict[JobKind, JobHandler] = {
            JobKind.CHAT_REPLY: replies,
            JobKind.CHAT_REGENERATE: replies,
            JobKind.CHARACTER_DRAFT: drafts,
            JobKind.MEMORY_EXTRACTION: memories,
            JobKind.MEMORY_REEVALUATION: memories,
            JobKind.SUMMARY_CHECKPOINT: summaries,
        }
        if image_runtime is not None:
            handlers[JobKind.IMAGE_REQUEST] = ImageGenerationService(
                engine,
                runtime,
                image_runtime,
                app.state.media,
                app.state.events,
                worker.images,
                worker.replies,
                context=worker.context,
            )
        scheduler = JobScheduler(
            engine, JobQueue(), handlers, worker.leases, worker.current_mode, mode_switch=worker.mode_switch
        )
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
    media_store: MediaStore | None = None,
    daily_backup: DailyBackup | None = None,
) -> FastAPI:
    """Returns the FastAPI application for the given settings, which it keeps in `app.state.settings`.

    `engine` is the verified and migrated database, kept in `app.state.engine`; without it `/health/ready` answers
    503. `media_store` holds the asset files, in `app.state.media`; by default it is the store at `/data/media`, which
    is touched only when a route writes or reads a file. `control_probe` tells whether the control API answers and may
    be replaced in tests. With `worker` and an `engine`, the application's lifespan runs the job worker; without them
    queued jobs wait. With `daily_backup`, kept in `app.state.daily_backup`, the lifespan also writes the daily
    backup of the data volume; tests leave it out. `app.state.events` is
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
    app.state.daily_backup = daily_backup
    app.state.scheduler = None
    app.state.media = media_store if media_store is not None else MediaStore(DEFAULT_MEDIA_ROOT)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(characters.router, prefix=API_PREFIX)
    app.include_router(assets.router, prefix=API_PREFIX)
    app.include_router(scenarios.router, prefix=API_PREFIX)
    app.include_router(conversations.router, prefix=API_PREFIX)
    app.include_router(messages.router, prefix=API_PREFIX)
    app.include_router(memories.router, prefix=API_PREFIX)
    app.include_router(diagnostics.router, prefix=API_PREFIX)
    app.include_router(images.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    app.include_router(events.router)
    app.include_router(media.router)
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
    becomes ready on a database it cannot trust. Otherwise empties the media staging folder and serves on port 8000
    with a single worker, which the in-process WebSocket hub and job worker require, until the server stops, and
    returns 0. The job worker streams
    replies from KoboldCpp and draws images with ComfyUI under leases of the control API, asking it for the GPU mode
    its queue needs, and DailyBackup writes one backup of /data a day while
    the backend is idle. The access log is off, because request paths and queries can carry user text.
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
    # Startup step 4 (docs/backend.md): no write is in flight yet, so whatever sits in the staging folder was cut.
    # A failure here is logged and does not stop the start; the files are only wasted space.
    media_store = MediaStore(MEDIA_ROOT)
    try:
        media_store.clear_staging()
    except OSError:
        logger.warning("media_staging_cleanup_failed", exc_info=True)
    orchestrator = OrchestratorClient(settings.control_token)
    worker = Worker(
        text_runtime=KoboldCppRuntime,
        leases=orchestrator,
        mode_switch=orchestrator,
        image_runtime=ComfyUiRuntime,
    )
    daily_backup = DailyBackup(DATA_ROOT, settings.zone)
    uvicorn.run(
        create_app(settings, engine, worker=worker, media_store=media_store, daily_backup=daily_backup),
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        log_config=None,
        access_log=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
