# Backend

Status: accepted (2026-11-01). Implementation: partial. Delivered in P01.3a: the package files, `settings.py`, `logs.py`, the application factory in `main.py`, the Dockerfile with its test stage, and the quality gates. Delivered in P01.3b: the SQLite engine with the WAL and FTS5 check, the Alembic baseline, and the two health routes. Delivered in P01.4a: the orchestrator's `pyproject.toml`, `uv.lock`, a Dockerfile with its `lock`, `lockfile` and `test` stages but no runtime stage yet, and `state_machine.py` with its gates and tests. Delivered in P01.4b: `container_gate.py`, with HTTPX as the orchestrator's first runtime dependency, its tests, and the Docker socket scans of both packages. The rest is planned, phases P01 to P07.

The backend and the orchestrator are two separate Python 3.12 packages. Neither imports
the other. They share only the [control API](api.md#control-api).

## Package layout

```text
backend/
  pyproject.toml  uv.lock  alembic.ini  Dockerfile
  migrations/
  src/lyrveil/
    main.py              Composition root: builds adapters, services and the app
    settings.py          Typed environment settings
    logs.py              JSON logging with the field allowlist
    cli.py               backup, export-backups, restore-check, restore (P01.6d); rebuild-search, drain (planned)
    tools/               One-shot tools: models.py (downloads), import_model.py (copies a custom model file)
    api/                 Routers, request and response schemas, error mapping, WebSocket
    domain/              Entities, enums, state rules. Pure, no I/O
    ports/               Protocols: TextRuntime, ImageRuntime, MemoryRetriever, Clock...
    services/            CharacterService, ConversationService, MessageService...
    memory/              MemoryManager, extraction, retrieval, consistency, summaries
    consistency/         CharacterStateManager, ReplyNormalizer, reply checks, content guard
    context/             ContextBuilder, token budgets, length bands
    prompts/             PromptTemplateService, templates and pattern lists by language, structured schemas
    images/              ImageGenerationService, VisualConsistencyService, tag vocabulary, sanitizer, workflow loader
    jobs/                JobScheduler, job handlers, retry policy
    scheduling/          Proactive evaluation and policies
    persistence/         SQLAlchemy models, repositories, PersistenceService
    runtimes/            KoboldCppRuntime, ComfyUiRuntime
    orchestration/       OrchestratorClient
    media/               MediaStore: staging, atomic moves, thumbnails
    events/              EventBus, WebSocket hub with replay buffer
  tests/

orchestrator/
  pyproject.toml  uv.lock  Dockerfile
  src/lyrveil_orchestrator/
    main.py  settings.py
    api.py               Control API
    state_machine.py     Transition table, pure
    lifecycle.py         ModelLifecycleManager
    gpu_monitor.py       GPUResourceMonitor
    supervisor.py        ProcessSupervisor
    container_gate.py    Allowlisted Docker Engine calls, sole socket user
    preflight.py
  tests/
```

## Services

| Service | Package | Responsibility |
| --- | --- | --- |
| `CharacterService` | `services` | CRUD, versioning, diff, restore, locks, duplicate, import and export. Sole writer of canonical data. |
| `CharacterCreatorService` | `services` | Natural-language drafts, draft validation, avatar requests. |
| `ConversationService` | `services` | Chat list, creation, pinning, archive, deletion, read state, unread counts. |
| `MessageService` | `services` | Sending, variants, edits, deletion, search. Notifies derived data on changes. |
| `MemoryManager` | `memory` | Facade over the memory engine for the rest of the backend, and Memory Studio operations. |
| `MemoryExtractionService` | `memory` | Trigger rules, windows, extraction jobs, idempotency, storing results. |
| `MemoryRetrievalService` | `memory` | `FtsMemoryRetriever`, scoring, budget packing. |
| `MemoryConsistencyService` | `memory` | Dedup, conflict classification, tombstones, invalidation after message changes. |
| `SummaryService` | `memory` | Progressive checkpoints and rollups. |
| `CharacterStateManager` | `consistency` | Per-conversation state, bounded updates, replay from history. |
| `ReplyNormalizer` | `consistency` | Rules N1 to N5 on the streamed text, the same for every profile ([Reply normalization](consistency-engine.md#reply-normalization)). |
| `ContextBuilder` | `context` | Builds the prompt in four parts within the token budget: stable system text, history, dynamic context with the anchor, current message ([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)). |
| `PromptTemplateService` | `prompts` | Loads versioned templates by language, renders them, provides grammars. |
| `ChatInferenceService` | `services` | Runs a reply: lease, context, stream through the normalizer, [reply checks](consistency-engine.md#reply-checks), at most one corrective regeneration, persistence. |
| `ImageGenerationService` | `images` | Image jobs by stage, ComfyUI progress, persistence. |
| `VisualConsistencyService` | `images` | Visual PromptBuilder, structured identity, wardrobe, tag vocabulary, sanitizer, scene filter, seed policy. |
| `JobScheduler` | `jobs` | Durable queue, selection, mode requests, retries, cancellation, preemption. |
| `SchedulerService` | `scheduling` | Evaluates proactive rules and creates jobs. |
| `NotificationService` | `services` | In-app notifications and their events. |
| `PersistenceService` | `persistence` | Units of work, backups, purges, integrity checks. |
| `ModelLifecycleManager` | orchestrator | GPU state machine, leases, mode changes, recovery. |
| `GPUResourceMonitor` | orchestrator | NVML sampling, release verification, preflight facts. |
| `ProcessSupervisor` | orchestrator | Starts and stops the two runtimes through `ContainerGate`, reads health and exit codes. |

`SummaryService`, `SchedulerService` and `ContainerGate` are additions to the required
list, kept separate so that no class becomes monolithic.

## Ports

Services depend on these `typing.Protocol` interfaces. Adapters implement them, and fakes
implement them in tests.

```python
class TextRuntime(Protocol):
    """Generates text with the currently loaded model.

    Callers must hold a valid llm lease. Raises RuntimeUnavailable when the runtime
    does not respond and RuntimeOutOfMemory on a CUDA allocation failure.
    """

    async def count_tokens(self, text: str, *, special: bool = False) -> int: ...
    def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]: ...
    async def generate(self, request: TextRequest) -> str: ...
    async def abort(self, genkey: str) -> None: ...
    async def info(self) -> TextRuntimeInfo: ...


class ImageRuntime(Protocol):
    """Runs one workflow at a time. Callers must hold a valid image lease."""

    async def submit(self, workflow: Workflow, client_id: str) -> str: ...
    def watch(self, prompt_id: str, client_id: str) -> AsyncIterator[ImageProgress]: ...
    async def fetch_outputs(self, prompt_id: str) -> list[ImageFile]: ...
    async def interrupt(self) -> None: ...
    async def info(self) -> ImageRuntimeInfo: ...


class LifecycleClient(Protocol):
    """Requests GPU modes and leases from the orchestrator."""

    async def state(self, since: int | None = None, wait_s: float = 0) -> GpuState: ...
    async def set_desired_mode(self, mode: GpuMode, job_id: str | None) -> None: ...
    async def acquire(self, mode: GpuMode, job_id: str) -> Lease: ...
    async def heartbeat(self, lease: Lease) -> None: ...
    async def release(self, lease: Lease, outcome: LeaseOutcome) -> None: ...
```

Other ports: `MemoryRetriever`
([contract](memory-engine.md#retrieval-contract)), `TokenCounter`, `Clock`, `MediaStore`,
`EventPublisher`, `UnitOfWork`.

Adding a model means writing a new adapter or changing a model file. No service changes.

## Concurrency model

- **One process, one event loop.** The container command `python -m lyrveil.main` runs
  Uvicorn with one worker. The WebSocket hub, the scheduler and the job worker live in the
  same process, so a single worker is an invariant. The command never sets `--workers`.
  `fastapi run` is not used, because the FastAPI CLI brings packages the backend does not
  need, and starting Uvicorn from `main.py` lets settings fail before the server starts.
- **REST handlers** that touch the database are plain `def`, so FastAPI runs them in its
  thread pool.
- **Job handlers and streaming** are `async`. They reach the database through
  `anyio.to_thread.run_sync` ([ADR 0009](decisions/0009-synchronous-sqlalchemy.md)).
- **One job runs at a time** for the GPU. Jobs with `required_mode = none` also run on the
  worker.
- **Writes** use short transactions opened with `BEGIN IMMEDIATE`. A streaming reply never
  keeps a transaction open.

### Streaming persistence

While a reply streams, deltas go to the WebSocket as soon as `ReplyNormalizer` releases them.
It holds back only the first 40 characters and any open asterisk span. The variant text is written to
the database every 2 seconds, and at the end. A corrected check that fires publishes `message.restarted`,
and the second generation overwrites the same variant. After a crash the reply keeps what was
written, its status becomes `interrupted` and the interface offers regeneration.

### Startup sequence

1. Load settings, configure logging.
2. Open the database and verify capabilities.
3. Back up if migrations are pending, then migrate.
4. Remove unfinished files from `data/media/.staging` and orphan media.
5. Requeue `running` jobs, mark streaming variants `interrupted`.
6. Fetch missed lifecycle events, start the state long poll.
7. Start the WebSocket hub, job worker and proactive evaluator.
8. Report ready.

Delivered so far: steps 1 and 2, and step 3 without the backup, because the backup service is planned and the
baseline has no data to protect. Steps 4 to 7 are planned. `main()` stops with exit status 69 and the log event
`database_capability_missing` (error code `DB_CAPABILITY_MISSING`) when WAL or FTS5 is missing, and with
`database_unavailable` when the file cannot be opened or migrated, so the server never starts on a database it cannot trust.

### Database and health

`persistence/database.py` builds the synchronous engine for `/data/db/lyrveil.sqlite3`
([ADR 0009](decisions/0009-synchronous-sqlalchemy.md)). Every new connection applies the pragmas of the
[data model](data-model.md#connection-settings). The driver is put in manual transaction mode: a transaction begins
with `BEGIN`, or with `BEGIN IMMEDIATE` when the connection has the `WRITE` execution option, so a writer takes the
write lock at the start instead of failing on a lock upgrade.

`verify_capabilities` reads the facts back instead of assuming them: the journal mode that SQLite reports after
the `WAL` request, and a probe that creates an `fts5` virtual table in the temporary database. It raises
`DatabaseCapabilityError` naming whatever is missing.

`persistence/migrations.py` runs Alembic on the engine (`upgrade_to_head`) and reads the state (`migration_state`).
`backend/alembic.ini` and `backend/migrations/` sit beside `src/` in the repository and in the image, so the command
line works too: `alembic -x database=<path> upgrade head`, with `/data/db/lyrveil.sqlite3` as the default. The
baseline revision `0001` creates `application_settings` only. Each later table arrives in the revision of the phase
that needs it.

Health routes, under `/api/v1`:

| Route | Answer |
| --- | --- |
| `GET /health/live` | `200` with `{"status": "alive"}` whenever the process serves requests |
| `GET /health/ready` | `200` when WAL, the migrations at head and FTS5 all hold, `503` otherwise. The body lists `checks.database` (with `journal_mode`), `checks.migrations` (`current` and `head`) and `checks.fts5`, each with `ok`. `control_api.reachable` comes from `GET /v1/health` of the orchestrator on the control network, with a one second timeout. It never changes the status |

The checks run on every request, so the answer follows the live state of the file. A driver error is reported as a
failed check without its message.

The OpenAPI document of the application is committed as `backend/openapi.json`, the contract the frontend generates its
types from. `lyrveil/openapi_document.py` builds and renders it, and `tests/unit/test_openapi_document.py` fails when the
committed file differs from what the application serves, with the regeneration command in its message. After an API
change, run `docker build -f backend/Dockerfile --target openapi --output type=local,dest=backend .`, then
`npm run api:generate --prefix frontend`, and commit both.

### Shutdown sequence

On SIGTERM: stop accepting jobs, flush the running reply, release the lease, close
WebSockets and exit. The `stop.cmd` drain runs earlier, so this is normally quick.

## Job handlers

| Kind | Handler flow |
| --- | --- |
| `chat_reply`, `chat_regenerate` | Lease, `ContextBuilder`, stream through `ReplyNormalizer`, reply checks, at most one corrective regeneration, commit, release, then extraction trigger. |
| `character_draft` | Lease, structured generation, safety check, store draft in `result_json`. |
| `image_request` | Stages in [Image generation](image-generation.md#job-stages). |
| `visual_profile_translate` | Lease, structured generation of `VisualIdentityResult` under a vocabulary grammar, sanitizer, store the identity slots and wardrobe tags that are not locked. |
| `image_proposal` | Lease, structured generation, store a proposal when valid. |
| `proactive_message` | Stages `decide` and `generate`. See [Scheduler](scheduler.md#generation-flow). |
| `summary_checkpoint` | Lease, structured generation, store a checkpoint. |
| `memory_extraction`, `memory_reevaluation` | Lease, structured generation, consistency rules, commit with cursor. |
| `maintenance` | `required_mode = none`. Retention, orphan cleanup, expiry of memories. |

### Retry policy

| Failure | Attempts | Backoff |
| --- | --- | --- |
| `chat_reply`: runtime failure, `LEASE_LOST` | 2 | 5 s |
| `image_request`: render failure | 2, the second with tiled decode | 10 s |
| Structured output invalid | 2 within the same job, then the job retry policy | None |
| Extraction and summaries | 3 | 1 min, 5 min, 30 min |
| Preemption of background work | No attempt consumed | None |

## Logging

JSON on stdout through structlog. Docker rotates with `max-size: 10m`, `max-file: 5`.

Fields: `ts`, `level`, `service`, `event`, `request_id`, `job_id`, `conversation_id`,
`character_id`, `state`, `duration_ms`, `error_code`.

Never logged at any level: message content, prompts, memory text, image prompts, uploaded
file names, tokens. `DEBUG` adds sizes and timing, not content. A unit test sends a marker
string through the main paths and asserts that it never reaches a log record.

`logs.py` enforces this with an allowlist, the last step before rendering, for structlog and
standard library records alike:

- `event` is the only free-text field, and code passes a constant event name;
- the identifier fields above keep a value only when it is a short token of at most 64
  characters without spaces, otherwise `[dropped]`;
- numeric fields whose name ends in `_ms`, `_bytes`, `_chars`, `_tokens` or `_count` are kept;
- an exception becomes `error_type` and `traceback`, a list of file, line and function. Its
  message is left out, because messages such as validation errors can quote user text;
- every other field is removed, and its name is listed in `dropped_fields`.

The Uvicorn access log is off, because request paths and queries can carry user text.

## Settings

Environment settings are immutable and typed in `settings.py`. Runtime settings live in
`application_settings`. See [Configuration](configuration.md).

`load_settings()` reads them once at startup. A missing or invalid variable stops the process
before logging and the server start: it prints each variable name with the reason, never the
value, on stderr and exits with status 78.

## Quality gates

```bash
ruff check .
ruff format --check .
mypy --strict src tests
pytest tests/unit
pytest tests/integration
```

They run in containers of the `test` stage of `backend/Dockerfile`, started by
`scripts\lyrveil.cmd test lint`, `test unit` and `test integration` ([Testing](testing.md#the-lint-level)). Tool
settings are in `backend/pyproject.toml`; mypy uses the Pydantic plugin.

An import-rule test, `tests/unit/test_import_rules.py`, fails when `domain` imports another
layer, when `services` imports `persistence.models` or `runtimes`, or when `api` imports
`runtimes`. It reads imports with `ast`, resolves relative imports, and its own cases show
that each rule reports a violation.

`tests/unit/test_docker_socket.py` fails when a module of `src/` or `migrations/` could open
the Docker socket, which only the orchestrator's container gate uses
([Security](security.md#docker-socket)). It runs the scan of the orchestrator's
`tests/unit/test_socket_confinement.py`, described under [Orchestrator package](#orchestrator-package),
and expects no finding. The two copies change together, since the packages share no code.

### Dependencies and image

`backend/uv.lock` pins every package with its hashes, for Linux and Python 3.12 only. The
project itself is not built: `[tool.uv] package = false`, and the image puts `src` on
`PYTHONPATH`, so no unpinned build backend is downloaded. `uv` is not installed on Windows,
so the lock is regenerated in a container after a change to `pyproject.toml`:

```powershell
docker build -f backend/Dockerfile --target lockfile --output type=local,dest=backend .
```

The stages of `backend/Dockerfile`, all on `python:3.12.16-slim-trixie`:

| Stage | Content |
| --- | --- |
| `lock`, `lockfile` | Resolve `uv.lock` and export it |
| `deps` | `uv sync --locked --no-dev`: fails when the lock does not match `pyproject.toml` |
| `test` | Adds the `dev` group with `uv sync --locked`, `alembic.ini`, `migrations/`, the sources and `tests/` |
| `runtime` | The `deps` environment, `alembic.ini`, `migrations/` and the sources only, user `10001`, port 8000. The default target |

A unit test of the test stage checks that every installed distribution has the version
`uv.lock` pins.

### Orchestrator package

`orchestrator/` follows the same rules: `pyproject.toml` with the same ruff, mypy and pytest
settings, `package = false`, a Linux-only `uv.lock`, and a Dockerfile with the same `lock`,
`lockfile`, `deps` and `test` stages. The test stage adds `src/` and `tests/`. The runtime
dependencies so far are HTTPX 0.28.1, for the container gate, FastAPI 0.162.0, for the
control API, and nvidia-ml-py 13.615.71, for the GPU monitor. `[tool.uv]` holds Pydantic,
which FastAPI brings, at 2.14.2 with `constraint-dependencies`, the backend's version, and
mypy skips the untyped `pynvml` import. The `dev` group adds Hypothesis 6.170.14 and respx
0.23.1. Async tests run with the pytest plugin of AnyIO, which HTTPX brings, so
pytest-asyncio is not needed. The `runtime` stage, with `main.py` and Uvicorn, is planned
for the item that adds the orchestrator service to Compose. The lock is regenerated with:

```powershell
docker build -f orchestrator/Dockerfile --target lockfile --output type=local,dest=orchestrator .
```

The same gates run in its test stage through `test lint`, `test unit` and
`test integration`. `tests/unit/test_state_machine.py`
holds T-ORC-01 and checks that `state_machine.py` imports only `collections.abc`,
`dataclasses`, `enum` and `types`.

`container_gate.py` is the only module that talks to Docker
([Security](security.md#docker-socket), [Runtime contracts](runtime-contracts.md#docker-engine-api)).
`ContainerGate` has five async methods, `inspect`, `start`, `stop`, `top` and `logs`, plus
`aclose`. Each takes a `ContainerName` member and checks the container's identity before it
acts. `tests/unit/test_container_gate.py` holds T-ORC-07, with the Engine stubbed by respx
and one real Unix socket. It also checks that the gate imports only HTTPX, as a module, and
`collections.abc`, `dataclasses`, `enum`, `struct`, `types` and `typing`, and that it makes
one HTTPX client over one transport. `tests/unit/test_socket_confinement.py` fails when
another module of the package could open the Docker socket: it looks for socket and Docker
client modules, Unix-socket calls, the `uds` argument, the socket's path or a `unix://` URL,
`DOCKER_HOST`, and the `docker` command, alone or followed by an option or a subcommand. A
message that only names Docker, such as `Docker did not answer`, is not reported.

`gpu_monitor.py` reads the card through the `GpuProbe` port. `NvmlProbe` makes only the NVML
calls of [Runtime contracts](runtime-contracts.md#nvml-and-wsl2), on device 0, and raises
`GpuProbeError` for any NVML failure, a missing library included. `GPUResourceMonitor`
returns `GpuFacts`, with the device name, the driver and CUDA driver versions and a
`VramReading` in MiB, or a fresh `VramReading` alone. Release verification and the VRAM
checks before a load come with the lifecycle manager. `preflight.py` judges the facts at the
thresholds of [Operations](operations.md#preflight-checks), and `api.py` builds the
[control API](api.md#control-api) with `create_app(control_token, monitor)`.
`tests/unit/test_gpu_monitor.py` and `tests/unit/test_preflight.py` test both with fakes,
and `tests/integration/test_control_api.py` holds T-ORC-10.
