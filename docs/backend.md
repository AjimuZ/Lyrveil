# Backend

Status: accepted (2026-11-01). Implementation: partial. Delivered in P01.3a: the package files, `settings.py`, `logs.py`, the application factory in `main.py`, the Dockerfile with its test stage, and the quality gates. Delivered in P01.3b: the SQLite engine with the WAL and FTS5 check, the Alembic baseline, and the two health routes. Delivered in P01.4a: the orchestrator's `pyproject.toml`, `uv.lock`, a Dockerfile with its `lock`, `lockfile` and `test` stages but no runtime stage yet, and `state_machine.py` with its gates and tests. Delivered in P01.4b: `container_gate.py`, with HTTPX as the orchestrator's first runtime dependency, its tests, and the Docker socket scans of both packages. Delivered in P02.3a: the `TextRuntime` port and `KoboldCppRuntime` ([Text runtime](#text-runtime)). Delivered in P02.3b: `PromptFormatter` for the three template families ([Prompt formatter](#prompt-formatter)). Delivered in P02.4: the `generation_jobs` table, `JobQueue`, `JobScheduler` with leases and heartbeats, and the restart recovery ([Job queue](#job-queue)). Delivered in P02.5c: `events/hub.py` with the replay buffer, the events of `/ws`, `ChatInferenceService` as the handler of `chat_reply` and `chat_regenerate`, the job worker in the application's lifespan and the recovery of interrupted replies ([Streaming persistence](#streaming-persistence)). Delivered in P03.3: `prompts/gbnf.py` and `prompts/structured.py` for structured output, and the `character_draft` handler ([Job handlers](#job-handlers)). Delivered in P03.6a: `consistency/content_guard.py`, the content guard for character data ([Security](security.md#character-data)). The rest is planned, phases P01 to P07.

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
    catalog.py           Validator of config/model-catalog.json and the tier defaults (P02.1a)
    cli.py               backup, export-backups, restore-check, restore (P01.6d); rebuild-search, drain (planned)
    tools/               One-shot tools: models.py (downloads), import_model.py (copies a custom model file)
    api/                 Routers, request and response schemas, error mapping, WebSocket
    domain/              Entities, enums, state rules. Pure, no I/O
    ports/               Protocols: TextRuntime, ImageRuntime, MemoryRetriever, Clock...
    services/            CharacterService, ConversationService, MessageService...
    memory/              MemoryManager, extraction, retrieval, consistency, summaries
    consistency/         CharacterStateManager, ReplyNormalizer, reply checks, content guard
    context/             ContextBuilder, token budgets, length bands; minimal.py, the P02 reply context
    prompts/             PromptTemplateService, templates and pattern lists by language, structured schemas
    images/              ImageGenerationService, VisualConsistencyService, tag vocabulary, sanitizer, workflow loader
    jobs/                JobScheduler, job handlers, retry policy
    scheduling/          Proactive evaluation and policies
    persistence/         SQLAlchemy models, repositories, PersistenceService
    runtimes/            KoboldCppRuntime, ComfyUiRuntime
    orchestration/       OrchestratorClient
    media/               MediaStore: staging, atomic moves, checked paths; Pillow checks, crops, thumbnails
    events/              EventHub: event ids, replay buffer, one bounded queue per WebSocket connection
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
| `CharacterCreatorService` | `services` | Natural-language drafts, draft validation, avatar requests. Delivered in P03.3: the drafts, as the `character_draft` handler. |
| `CharacterAssetService` | `services` | Character assets: upload, crop, list, primary avatar, delete, and the files of the media routes. Delivered in P03.4a, without generated avatars (P05). |
| `ConversationService` | `services` | Chat list, creation, pinning, archive, deletion, read state, unread counts. |
| `MessageService` | `services` | Sending, variants, edits, deletion, search. Notifies derived data on changes. Delivered in P02.5b: send, edit, delete, regenerate, variants and history paging; the notification joins with P04.7 and search with P02.5d. |
| `MemoryManager` | `memory` | Facade over the memory engine for the rest of the backend, and Memory Studio operations. |
| `MemoryExtractionService` | `memory` | Trigger rules, windows, extraction jobs, idempotency, storing results. |
| `MemoryRetrievalService` | `memory` | `FtsMemoryRetriever`, scoring, budget packing. |
| `MemoryConsistencyService` | `memory` | Dedup, conflict classification, tombstones, invalidation after message changes. |
| `SummaryService` | `memory` | Progressive checkpoints and rollups. |
| `CharacterStateManager` | `consistency` | Per-conversation state, bounded updates, replay from history. |
| `ReplyNormalizer` | `consistency` | Rules N1 to N5 on the streamed text, the same for every profile ([Reply normalization](consistency-engine.md#reply-normalization)). |
| `ContextBuilder` | `context` | Builds the prompt in four parts within the token budget: stable system text, history, dynamic context with the anchor, current message ([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)). |
| `PromptTemplateService` | `prompts` | Loads versioned templates by language, renders them, provides grammars. |
| `ChatInferenceService` | `services` | Runs a reply: lease, context, stream through the normalizer, [reply checks](consistency-engine.md#reply-checks), at most one corrective regeneration, persistence. Delivered in P02.5c: the minimal context, the stream and the persistence, under the lease the scheduler holds; the normalizer, the checks and the corrective generation join with P04.8. |
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

    Callers must hold a valid llm lease. Raises RuntimeUnavailableError when the runtime
    does not respond and RuntimeOutOfMemoryError on a CUDA allocation failure.
    """

    async def count_tokens(self, text: str, *, special: bool = False) -> int: ...
    def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]: ...
    async def generate(self, request: TextRequest) -> str: ...
    async def abort(self, genkey: str) -> bool: ...
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
`EventPublisher`, `UnitOfWork`. `EventPublisher` is delivered in `ports/events.py` (P02.5c), with `EventHub` as its
adapter.

Adding a model means writing a new adapter or changing a model file. No service changes.

### Text runtime

Delivered in P02.3a: `ports/text_runtime.py` holds `TextRuntime` with its types and errors, and
`runtimes/koboldcpp.py` holds `KoboldCppRuntime`. Since P02.5c the job worker opens it for the application's life and
`ChatInferenceService` streams replies through it.
Delivered in P02.3b: `prompts/formatter.py` renders the prompt text that the adapter sends
([Prompt formatter](#prompt-formatter)).

- `TextRequest` carries only the [GenerationInput fields](runtime-contracts.md#generationinput-fields-used).
  A field left at `None` is not sent, so the runtime's default applies. `genkey` is required.
- `stream` yields each non-empty token in order as a `TextChunk`. The last chunk has `finish_reason`
  `stop`, `length` or `aborted`, and `text`, the whole reply. `aborted` means `abort` named the
  generation's `genkey` while it ran.
- `count_tokens` returns `value` of `POST /api/extra/tokencount`, with `special` false by default, so
  no start token is counted. It never estimates.
- `info` returns the model name without `koboldcpp/`, the reported version and the context size read
  from `value` of `GET /api/extra/true_max_context_length`. The contract does not name that field: the
  name comes from the fake and is unconfirmed until the GPU suite. Comparing the version with the pin
  is the caller's job.
- The adapter calls only the paths in its `ENDPOINTS`, all from [Runtime contracts](runtime-contracts.md#endpoints),
  and not `GET /api/extra/perf`. The client ignores proxy settings and follows no redirect.

Timeouts are the [GPU orchestration](gpu-orchestration.md#timeouts) defaults: 180 s in total for a
generation, and 60 s without a stream line. Past either one, the adapter aborts the generation's `genkey`,
then raises. Other calls, and connecting, wait 10 s. That value is the adapter's own, because the table has none.

| Failure | Error | `code` |
| --- | --- | --- |
| No answer, refused or dropped connection, undocumented status | `RuntimeUnavailableError` | `RUNTIME_UNAVAILABLE` |
| Stream ends or breaks before its closing event | `RuntimeStreamDroppedError`, a subclass of the above | `RUNTIME_UNAVAILABLE` |
| Invalid JSON, or an answer without the documented fields | `RuntimeResponseInvalidError`, a subclass of the above | `RUNTIME_UNAVAILABLE` |
| Error answer whose body says "out of memory", or a closing event with `finish_reason` `error` | `RuntimeOutOfMemoryError` | `LLM_OOM` |
| No stream line within the stall timeout | `GenerationStalledError` | `GENERATION_STALLED` |
| Past the total timeout | `GenerationTimeoutError` | `GENERATION_TIMEOUT` |

The out-of-memory row is an assumption shared with `fake_kobold` ([Testing](testing.md#fake-runtimes)).
The contract does not record how KoboldCpp reports it, and any closing `error` counts as out of memory.
Error messages hold paths, status codes and reasons, never prompt or reply text.

`tests/unit/test_koboldcpp_runtime.py` runs the adapter against `fake_kobold` over real HTTP. It also
reads the contract page and fails when the adapter's module names an `/api/` path missing from the
KoboldCpp endpoint table, or when a request sends a field outside the GenerationInput table.

### Prompt formatter

Delivered in P02.3b: `prompts/formatter.py`, the [Prompt format](prompting.md#prompt-format) of each family.
Since P02.5c `ChatInferenceService` renders replies with it; `ContextBuilder` takes over the parts in P04.5.

- `formatter_for(family)` returns the formatter of a catalog `template`: `gemma4`, `mistral_v3_tekken` or `chatml`.
- `chat(parts, current_message)` and `proactive(parts, instruction)` take a `ChatParts`: the stable text, the
  history as `Turn` values, blocks 4, 5 and 9, the character's first name and the user's display name. They return
  `text` and `prefix`, the start of `text` that ends where the last user turn begins. For `gemma4` and `chatml` the
  prefix holds the stable text and the history, for `mistral_v3_tekken` only the history.
- `structured(system_text, user_text)` returns one system text and one user turn, with no frame.
- `stop_sequences(user_display_name)` returns the family's stop sequences.
- `neutralize_frame_markers` replaces the `[` of a line that starts with `[Contesto per` or `[Messaggio di`,
  in any case and after optional spaces, with `(`. Every text the formatter receives passes through it, so only the
  formatter writes the markers.

Choices the docs did not fix, kept until the GPU suite (T-GPU-03, T-GPU-04) checks them:

- An empty block 4, 5 or 9 is left out of the frame, and trailing line breaks of a block are dropped. Turn text is
  kept as is.
- Each history turn is written by its own role, so two character turns in a row, such as a proactive message after a
  reply, stay two turns. In `mistral_v3_tekken` that gives two `{reply}</s>` pieces, an order the GGUF template refuses.

`tests/unit/test_prompt_formatter.py` holds T-MOD-02, with golden strings written from the formats of
[Prompt format](prompting.md#prompt-format), and T-MOD-05.

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

Delivered in P02.5c, in `services/chat_inference.py`, with the event side in `events/hub.py`
([APIs](api.md#event-websocket)):

| Moment | Database | Events |
| --- | --- | --- |
| Run starts | Message `streaming`, text emptied; the payload's variant stays `streaming` | `typing.started` |
| Each token | Written every 2 s (`FLUSH_INTERVAL_S`), each time in its own short transaction | `message.delta` |
| Runtime finishes | Variant and message `complete` with the whole text; `interrupted` when the runtime reports `aborted` | `typing.stopped`, `message.completed` |
| Failure, retry coming | Message back to `queued`, text emptied, so the next run starts clean | `typing.stopped`, `message.updated` |
| Final failure | Variant and message `failed`, with the text streamed so far | `typing.stopped`, `message.failed` |
| Shutdown cancels the run | Text flushed, reply left `streaming` | None |
| Next start | Every `streaming` message and its active variant become `interrupted`; their running job ends `failed` with `JOB_INTERRUPTED` | None |

- The handler decides "retry coming" with the queue's own rule: a retryable failure (a `TextRuntimeError`, a lost lease,
  or a `JobFailedError` marked retryable) while `attempt` is below the kind's maximum. Any other exception is a final
  `INTERNAL` failure. A preemption always counts as a retry.
- A cancellation during a run is a lost lease (`LEASE_LOST`), the only cancellation the scheduler makes, unless the
  lifespan has called `close()` first, which marks the shutdown.
- A run whose variant is no longer waiting (finished by a run whose job state a restart lost, replaced, or deleted) is
  done at once with `{"skipped": true}`, without touching the runtime. A reply whose chat was deleted ends `failed`.
- The prompt is `context/minimal.py`: block 1 and block 2 of [Prompting](prompting.md#system-blocks) with the card
  fields P02 stores, then the chat's `complete` and `interrupted` messages with text, at most the newest 100, and the
  newest user message before the reply as the current one. The oldest turns are dropped, a quarter at a time, until the
  runtime's own token count (BOS included) plus `max_length` fits the context size the runtime reports. Blocks 3, 4, 5
  and 9 are empty until P03.1 and P04.
- `ReplySettings` holds the documented defaults while no settings store exists: the `gemma4` family, the `gemma4` chat
  sampler preset, `max_length` 400 and the user `Utente`. Both tiers' default text profiles are `gemma4`, so another
  active profile is not followed yet.
- Choices the documents did not fix: a crash or a shutdown in the middle of a reply ends its job at the next start
  instead of queuing it again, so the partial text the user saw is not silently replaced; a reply still `queued` when
  the backend stops is queued again. With no user message left before the reply (the user deleted it) the run fails
  with `INTERNAL`.

`tests/integration/test_reply_streaming.py` holds T-MSG-03 (token order, the flush times against a fake clock, the
stored text), T-MSG-09, T-MSG-12 through the application's `/ws` with its lifespan worker, T-MSG-13 through a restart
of `main()`, the retry path and T-SEC-06. `tests/unit/test_event_hub.py` covers the replay rules and the bounded queue.

### Startup sequence

1. Load settings, configure logging.
2. Open the database and verify capabilities.
3. Back up if migrations are pending, then migrate.
4. Remove unfinished files from `data/media/.staging` and orphan media.
5. Requeue `running` jobs, mark streaming variants `interrupted`.
6. Fetch missed lifecycle events, start the state long poll.
7. Start the WebSocket hub, job worker and proactive evaluator.
8. Report ready.

Delivered so far: steps 1 and 2, step 3 without the backup, because the backup service is planned and the
baseline has no data to protect, step 5 ([Job queue](#job-queue), [Streaming persistence](#streaming-persistence)),
and step 7 for the WebSocket hub, built with the application, and the job worker, started by its lifespan. Step 4
is delivered for the staging folder only (P03.4a, `MediaStore.clear_staging`, run after step 5; a failure is logged as
`media_staging_cleanup_failed` and does not stop startup); orphan media cleanup, step 6 and the proactive evaluator of
step 7 are planned. Until step 6 the worker assumes the `llm` mode: a lease the
orchestrator refuses (`NOT_READY`) keeps a reply queued. In step 5 the replies are recovered before the other jobs,
because they end their own jobs. A failure of either recovery stops startup like a migration failure. `main()` stops with exit status 69 and the log event
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

### Local-only checks and headers

`api/security.py` holds the controls of [Security](security.md#local-only-model), and `create_app` installs them on
every application:

| Part | Behavior |
| --- | --- |
| `LocalOnlyMiddleware` | Refuses a `Host` outside `Settings.allowed_hosts` and `127.0.0.1:8000`, and an `Origin` outside `Settings.allowed_origins` on writes and WebSocket handshakes, with `403 FORBIDDEN_ORIGIN` in `application/problem+json`. It runs before any route. |
| `SecurityHeadersMiddleware` | Sets the [documented headers](security.md#http-headers) on every answer and WebSocket handshake answer, refusals included, with the WebSocket origins on `LYRVEIL_HTTP_PORT`. |
| `internal_error_handler` | Turns an unexpected exception into a generic `500 INTERNAL` with the same headers. Starlette runs it outside the middleware, so it sets them itself. |
| `api/events.py` | `/ws`: accepts the handshake, sends the `connected` frame with `app.state.boot_id`, answers the first `hello` with the replay or `resync.required` of `app.state.events`, then sends events; other client frames are ignored ([APIs](api.md#event-websocket)). |

A test client must use the app's address, for example `TestClient(app, base_url="http://localhost:4200")`: the default
`testserver` Host is refused. `tests/integration/test_local_only.py` is T-SEC-01.

### Shutdown sequence

On SIGTERM: stop accepting jobs, flush the running reply, release the lease, close
WebSockets and exit. The `stop.cmd` drain runs earlier, so this is normally quick.

Delivered in P02.5c: the lifespan stops the job worker and the running reply flushes its text and stays `streaming`,
for the next start to mark `interrupted`. Its lease is not released: it expires after its TTL.

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

Delivered in P03.3: `character_draft`, run by `CharacterCreatorService` (`services/character_creator.py`), registered in
the application's lifespan beside the reply handler. See [Assisted creation](character-system.md#assisted-creation).
Since P03.6a the safety check is the content guard, run inside the draft's validation so that a refusal gets the one
repair; a repair the guard refuses too fails the job with `CONTENT_POLICY_BLOCKED`, without a retry. The route checks
the description with the same guard before it queues the job, and the handler checks it again before it generates, for
a job queued before a list entry existed.

### Retry policy

| Failure | Attempts | Backoff |
| --- | --- | --- |
| `chat_reply`: runtime failure, `LEASE_LOST` | 2 | 5 s |
| `image_request`: render failure | 2, the second with tiled decode | 10 s |
| Structured output invalid | 2 within the same job, then the job retry policy | None |
| Extraction and summaries | 3 | 1 min, 5 min, 30 min |
| Preemption of background work | No attempt consumed | None |

### Job queue

Delivered in P02.4: revision `0002` creates `generation_jobs` ([Data model](data-model.md#generation_jobs)), and
`jobs/` holds the queue ([ADR 0010](decisions/0010-job-queue-and-scheduling-policy.md)). Since P02.5c the application's
lifespan runs the worker with `ChatInferenceService` for `chat_reply` and `chat_regenerate`, and a send or a
regeneration wakes it at once (`JobScheduler.notify`); jobs of other kinds wait. Mode requests, batching, fairness,
preemption across modes and cancellation come with P06.3.

| Module | Responsibility |
| --- | --- |
| `jobs/policy.py` | `JobKind`, `JobStatus`, `RequiredMode`, `JobClass`, and per kind the priority, class, maximum attempts, backoff and stages of [Job kinds and priorities](gpu-orchestration.md#job-kinds-and-priorities) |
| `jobs/queue.py` | `JobQueue`: every read and state change of `generation_jobs`, on a connection the caller owns |
| `jobs/handlers.py` | The handler contract: `RunContext`, the outcomes `JobDone` and `NextStage`, and `JobFailedError`, `JobPreemptedError` |
| `jobs/scheduler.py` | `JobScheduler`, the single worker, and `recover_interrupted_jobs` for startup step 5 |

Behavior:

- **Durable with its change.** `JobQueue.enqueue(connection, kind, payload, ...)` writes the job in the caller's
  transaction and never commits, so the job exists exactly when the change that created it does. A repeated
  `idempotency_key` returns the stored job. Past 200 queued and running jobs it raises `QueueFullError`.
- **Selection.** `next_eligible` returns the queued job with the lowest priority, then the oldest, then the lowest id,
  among the jobs whose stage needs one of the given modes and whose kind has a handler. A job whose `not_before` lies in
  the future, or with `cancel_requested_at` set, waits. The scheduler passes `none` and the mode whose runtime is ready,
  so a job of another mode waits instead of running.
- **Leases.** A stage that needs `llm` or `image` runs under a lease of that mode, taken before the job is marked
  `running`. A refused lease (`NOT_READY`, `LEASE_HELD`) or a control API that does not answer leaves the job queued. The
  lease is renewed every 10 s; a `410` cancels the handler and the job retries with `LEASE_LOST`, without a release. The
  release outcome is `oom` for `LLM_OOM` and `IMAGE_OOM`, `runtime_error` for `RUNTIME_UNAVAILABLE`,
  `GENERATION_STALLED` and `GENERATION_TIMEOUT`, and `ok` otherwise. Each run gets a new `genkey`.
- **Outcomes.** `JobDone(result)` completes the job. `NextStage(stage, payload)` queues it again in that stage, with the
  stage's mode, and resets `attempt` to 0, so each stage has the kind's full attempts: the `render` retry of an image
  is not used up by its `prompt` stage. `JobPreemptedError` queues it again at once and gives the attempt back.
- **Retries.** `JobFailedError(code, detail, retryable=...)` and a `TextRuntimeError`, which is retryable with its `code`,
  record a failed attempt. While attempts remain, a retryable failure queues the job again after the kind's backoff for
  that attempt; otherwise the job ends `failed`. `error_code` and `error_detail`, cut to 500 characters, keep the last
  reason in both cases. Any other exception ends the job at once with `INTERNAL`, because a bug does not heal on retry.
- **Restart.** At startup, before any worker, every `running` job is queued again at once in the stage it was running,
  with `JOB_INTERRUPTED` as its reason and its lease cleared. The interrupted run counts as an attempt, so a job that
  brings the process down cannot loop forever: one that was on its last attempt ends `failed` with `JOB_INTERRUPTED`.

Choices the documents did not fix:

- `stage` names the step that runs next, from which `required_mode` derives. [Job stages](image-generation.md#job-stages)
  says it records the last finished step; the two agree once a stage writes its results before the job moves on.
- Backoffs the retry table does not give: 5 s for `character_draft`, `visual_profile_translate` and
  `proactive_message`, like a reply. `image_proposal` and `maintenance` have one attempt and no backoff.
- Extraction and summaries list three waits, 1, 5 and 30 min, but have 3 attempts, so only the first two are used.
  The third applies if the maximum is raised.
- A handler writes its results in its own transactions and must be idempotent per stage: a restart between its commit
  and the scheduler's state change runs the stage again.
- The FKs of `conversation_id`, `character_id` and `message_id` arrive with the revision that creates those tables.

`tests/integration/test_job_queue.py` covers durability with the creating change and across a new engine, restart
recovery, the backoff and maximum of a reply and of extraction, priorities, mode eligibility, and the scheduler with a
fake lease provider: result, refused lease, `none` stage, out of memory, handler bug, new stage, preemption and a lost
lease. `tests/unit/test_job_policy.py` checks the policy table and the identifier and timestamp helpers of
`domain/identifiers.py`, which writes UUIDv7 keys and UTC text with milliseconds.

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
`uv.lock` pins. Since P02.3a, HTTPX 0.28.1 is a runtime dependency, the runtimes' client, and its AnyIO
gives the generation timeouts. Before that it was in the `dev` group only. Since P02.6c, websockets 17.4 is a
runtime dependency, the library Uvicorn needs to upgrade `/ws`; without it Uvicorn refuses every handshake. The test
stage also copies
`tests/fakes/` to `/app/fakes` and `docs/runtime-contracts.md`, and `"."` on the pytest path and the
mypy path makes `fakes` importable there.

### Orchestrator package

`orchestrator/` follows the same rules: `pyproject.toml` with the same ruff, mypy and pytest
settings, `package = false`, a Linux-only `uv.lock`, and a Dockerfile with the same `lock`,
`lockfile`, `deps` and `test` stages. The test stage adds `src/` and `tests/`. The runtime
dependencies so far are HTTPX 0.28.1, for the container gate, FastAPI 0.162.0, for the
control API, Uvicorn 0.54.0, which serves it, and nvidia-ml-py 13.615.71, for the GPU monitor.
`[tool.uv]` holds Pydantic,
which FastAPI brings, at 2.14.2 with `constraint-dependencies`, the backend's version, and
mypy skips the untyped `pynvml` import. The `dev` group adds Hypothesis 6.170.14 and respx
0.23.1. Async tests run with the pytest plugin of AnyIO, which HTTPX brings, so
pytest-asyncio is not needed.

Since P02.6c the `runtime` stage runs `python -m lyrveil_orchestrator.main`, the composition
root: it reads `LYRVEIL_CONTROL_TOKEN` and `LYRVEIL_LOG_LEVEL` (exit status 78 with the
variable's name, never its value, when one is invalid), builds `NvmlProbe`, `ContainerGate`,
`ProcessSupervisor` and `ModelLifecycleManager`, and serves the control API with one Uvicorn
worker on port 8100, with the access log off. No setting selects another adapter. The
`fake-runtime` stage, for `compose.test.yaml` only, adds `tests/fakes/` and starts
`fakes.fake_orchestrator`, which passes `FakeContainerGate` and `FakeGpuProbe` to the same
`run` function. The lock is regenerated with:

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
`VramReading` in MiB, or a fresh `VramReading` alone. `preflight.py` judges the facts at the
thresholds of [Operations](operations.md#preflight-checks), and `api.py` builds the
[control API](api.md#control-api) with `create_app(control_token, monitor, lifecycle)`.
`tests/unit/test_gpu_monitor.py` and `tests/unit/test_preflight.py` test both with fakes,
and `tests/integration/test_control_api.py` holds T-ORC-10.

`supervisor.py` holds `ProcessSupervisor`, the only caller of the gate's start and stop.
`start` refuses to start a runtime while the other one runs, then polls Docker health
until it reads `healthy`, every 2 s, within the load timeout. `stop` stops with the
runtime's grace period, then checks `State.Running`, the exit code and `top`, and samples
NVML every 500 ms until three samples lie within 64 MiB, for at most 60 s. Each failure is
a `SupervisorError` whose `code` comes from [Errors](api.md#errors). `lifecycle.py` holds
`ModelLifecycleManager`, which owns the state, the desired mode, the single lease and the
event buffer. It applies one transition of `state_machine.py` per step, and the lifespan of
`create_app` runs its loop. `tests/unit/test_supervisor.py` tests the supervisor, and
`tests/unit/test_leases.py` holds T-ORC-03. Both use the fakes of `tests/fakes/`.
`tests/integration/test_text_lifecycle.py` drives the control API from mode `llm` to
`LLM_READY`, through a lease, and back to `IDLE` on `POST /v1/drain`.

On the backend side, `lyrveil/orchestration/client.py` holds `OrchestratorClient`, which
uses the standard library and no proxy. `acquire_lease` raises `LeaseRefusedError` with
`NOT_READY` or `LEASE_HELD`. `renew_lease` raises `LeaseLostError` on `410`.
`release_lease` returns `False` on `410`, when nothing is left to release. Any other
failure raises `OrchestratorUnavailableError`. The client blocks, so callers run it in a
worker thread. `JobScheduler` is its caller, and heartbeats every 10 s ([Job queue](#job-queue)).
`tests/unit/test_orchestrator_client.py` tests it against a local HTTP server.
