# Backend

Status: accepted (2026-11-01). Implementation: planned, phases P01 to P07.

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
    cli.py               rebuild-search, drain, backup, restore-check
    api/                 Routers, request and response schemas, error mapping, WebSocket
    domain/              Entities, enums, state rules. Pure, no I/O
    ports/               Protocols: TextRuntime, ImageRuntime, MemoryRetriever, Clock...
    services/            CharacterService, ConversationService, MessageService...
    memory/              MemoryManager, extraction, retrieval, consistency, summaries
    consistency/         CharacterStateManager, reply validators, content guard
    context/             ContextBuilder, token budgets
    prompts/             PromptTemplateService, templates by language, structured schemas
    images/              ImageGenerationService, VisualConsistencyService, workflow loader
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
| `ContextBuilder` | `context` | Builds the prompt within the token budget. |
| `PromptTemplateService` | `prompts` | Loads versioned templates by language, renders them, provides grammars. |
| `ChatInferenceService` | `services` | Runs a reply: lease, context, stream, validators, persistence. |
| `ImageGenerationService` | `images` | Image jobs by stage, ComfyUI progress, persistence. |
| `VisualConsistencyService` | `images` | Visual PromptBuilder, identity tags, conflict filter, seed policy. |
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

- **One process, one event loop.** `fastapi run` with one worker. The WebSocket hub, the
  scheduler and the job worker live in the same process, so a single worker is an
  invariant. The container command never sets `--workers`.
- **REST handlers** that touch the database are plain `def`, so FastAPI runs them in its
  thread pool.
- **Job handlers and streaming** are `async`. They reach the database through
  `anyio.to_thread.run_sync` ([ADR 0009](decisions/0009-synchronous-sqlalchemy.md)).
- **One job runs at a time** for the GPU. Jobs with `required_mode = none` also run on the
  worker.
- **Writes** use short transactions opened with `BEGIN IMMEDIATE`. A streaming reply never
  keeps a transaction open.

### Streaming persistence

While a reply streams, deltas go to the WebSocket at once. The variant text is written to
the database every 2 seconds, and at the end. After a crash the reply keeps what was
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

### Shutdown sequence

On SIGTERM: stop accepting jobs, flush the running reply, release the lease, close
WebSockets and exit. The `stop.cmd` drain runs earlier, so this is normally quick.

## Job handlers

| Kind | Handler flow |
| --- | --- |
| `chat_reply`, `chat_regenerate` | Lease, `ContextBuilder`, stream, validators, commit, release, then extraction trigger. |
| `character_draft` | Lease, structured generation, safety check, store draft in `result_json`. |
| `image_request` | Stages in [Image generation](image-generation.md#job-stages). |
| `visual_profile_translate` | Lease, structured generation, store English tags unless locked. |
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

## Settings

Environment settings are immutable and typed in `settings.py`. Runtime settings live in
`application_settings`. See [Configuration](configuration.md).

## Quality gates

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src
uv run pytest
```

An import-rule test fails when `domain` imports another layer, when `services` imports
`persistence.models` or `runtimes`, or when `api` imports `runtimes`.
