# Backend

Status: accepted (2026-11-01). Implementation: partial. Delivered in P01.3a: the package files, `settings.py`, `logs.py`, the application factory in `main.py`, the Dockerfile with its test stage, and the quality gates. Delivered in P01.3b: the SQLite engine with the WAL and FTS5 check, the Alembic baseline, and the two health routes. Delivered in P01.4a: the orchestrator's `pyproject.toml`, `uv.lock`, a Dockerfile with its `lock`, `lockfile` and `test` stages but no runtime stage yet, and `state_machine.py` with its gates and tests. Delivered in P01.4b: `container_gate.py`, with HTTPX as the orchestrator's first runtime dependency, its tests, and the Docker socket scans of both packages. Delivered in P02.3a: the `TextRuntime` port and `KoboldCppRuntime` ([Text runtime](#text-runtime)). Delivered in P02.3b: `PromptFormatter` for the three template families ([Prompt formatter](#prompt-formatter)). Delivered in P02.4: the `generation_jobs` table, `JobQueue`, `JobScheduler` with leases and heartbeats, and the restart recovery ([Job queue](#job-queue)). Delivered in P02.5c: `events/hub.py` with the replay buffer, the events of `/ws`, `ChatInferenceService` as the handler of `chat_reply` and `chat_regenerate`, the job worker in the application's lifespan and the recovery of interrupted replies ([Streaming persistence](#streaming-persistence)). Delivered in P03.3: `prompts/gbnf.py` and `prompts/structured.py` for structured output, and the `character_draft` handler ([Job handlers](#job-handlers)). Delivered in P03.6a: `consistency/content_guard.py`, the content guard for character data ([Security](security.md#character-data)). Delivered in P04.2a: the `memory_extraction` handler and its trigger after a reply ([Job handlers](#job-handlers)). The rest is planned, phases P01 to P07.

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
                         The daily backup, services/daily_backup.py (P03.7b), writes the same archive
    tools/               One-shot tools: models.py (downloads), import_model.py (copies a custom model file)
    api/                 Routers, request and response schemas, error mapping, WebSocket
    domain/              Entities, enums, state rules. Pure, no I/O
    ports/               Protocols: TextRuntime, ImageRuntime, MemoryRetriever, Clock...
    services/            CharacterService, ConversationService, MessageService...
    memory/              MemoryManager, extraction, retrieval, consistency, summaries; text.py and stopwords/*.txt
                         (normalization, stop words, Snowball stems, safe FTS queries; P04.1; fingerprints, P04.2a),
                         window.py (extraction windows and triggers; P04.2a), consistency.py (the pure consistency
                         rules; P04.2b; the tombstone guard, P04.2c), retrieval.py (scoring, selection, labels
                         and past-intent cues; P04.3), the rest planned
    consistency/         CharacterStateManager, ReplyNormalizer, reply checks, content guard; state.py (the state
                         manager's bounded deltas and replay; P04.6)
    context/             ContextBuilder, token budgets, length bands, block texts, token counts
    prompts/             PromptTemplateService, templates and pattern lists by language, structured schemas
    images/              ImageGenerationService, VisualConsistencyService, tag vocabulary, sanitizer, workflow loader
    jobs/                JobScheduler, job handlers, retry policy
    scheduling/          Proactive evaluation and policies
    persistence/         SQLAlchemy models, repositories, PersistenceService
    runtimes/            KoboldCppRuntime, ComfyUiRuntime
    orchestration/       OrchestratorClient, GpuStateFollower
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
| `MessageService` | `services` | Sending, variants, edits, deletion, search. Notifies derived data on changes. Delivered in P02.5b: send, edit, delete, regenerate, variants and history paging; search with P02.5d; the notification with P04.7, through `InvalidationService` (`services/invalidation.py`) in the transaction of each change ([Delivered in P04.7](memory-engine.md#delivered-in-p047)). |
| `MemoryManager` | `memory` | Facade over the memory engine for the rest of the backend, and Memory Studio operations. |
| `MemoryExtractionService` | `memory` | Trigger rules, windows, extraction jobs, idempotency, storing results. |
| `MemoryRetrievalService` | `memory` | `FtsMemoryRetriever`, scoring, budget packing. Delivered in P04.3 as `FtsMemoryRetriever` in `services/memory_retrieval.py`, over `persistence/memory_retrieval.py` and the pure rules of `memory/retrieval.py`; no reply calls it until the context builder (P04.5). |
| `MemoryConsistencyService` | `memory` | Dedup, conflict classification, tombstones, invalidation after message changes. Delivered in P04.2b as `services/memory_consistency.py`, over the pure rules of `memory/consistency.py`: dedup, conflicts, the guards and expiry. P04.2c adds the tombstone guard. P04.7 adds the reactivation of invalidated memories and the age check; the invalidation itself is `InvalidationService`. User edits and deletions are in `services/memories.py` (`MemoryService`). |
| `SummaryService` | `memory` | Progressive checkpoints and rollups. |
| `CharacterStateManager` | `consistency` | Per-conversation state, bounded updates, replay from history. Delivered in P04.6 as `services/character_state.py`, over the pure rules of `consistency/state.py` and the SQL of `persistence/relationship.py`; extraction runs call it, and since P04.7 invalidation after a message change does ([Delivered in P04.6](consistency-engine.md#delivered-in-p046)). |
| `ReplyNormalizer` | `consistency` | Rules N1 to N5 on the streamed text, the same for every profile ([Reply normalization](consistency-engine.md#reply-normalization)). |
| `ContextBuilder` | `context` | Builds the prompt in four parts within the token budget: stable system text, history, dynamic context with the anchor, current message ([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)). |
| `PromptTemplateService` | `prompts` | Loads versioned templates by language, renders them, provides grammars. |
| `ChatInferenceService` | `services` | Runs a reply: lease, context, stream through the normalizer, [reply checks](consistency-engine.md#reply-checks), at most one corrective regeneration, persistence. Delivered in P02.5c: the stream and the persistence, under the lease the scheduler holds; since P04.5 the context is `ContextBuilder`'s; since P04.8a the stream goes through the normalizer; since P04.8b through the reply checks, with the corrective generation and the adaptive notes. |
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
adapter. `MemoryRetriever` is delivered in `ports/memory_retriever.py` (P04.3), with `FtsMemoryRetriever` as its
implementation.

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

### Image runtime

Delivered in P05.2a: `ports/image_runtime.py` holds `ImageRuntime` with its types and errors, and
`runtimes/comfyui.py` holds `ComfyUiRuntime`. No job uses it yet: the workflow templates and the image job come with
the rest of P05.

- `render(ImageRequest)` takes a workflow in API format and its `prompt_id`. It opens `WS /ws` with a new
  `clientId`, then sends `POST /prompt`, because ComfyUI only sends a client's messages to a connection that is already
  open. It yields `ImageProgress` events, then one `RenderFinished`: `success` with the output images of
  `GET /history/{prompt_id}`, or `interrupted` with none.
- `ProgressMap` turns messages into the [progress phases](image-generation.md#progress): `executing` of the
  `CheckpointLoaderSimple` node is `loading`; `progress` of a `KSampler` that starts from `EmptyLatentImage` is
  `sampling`, and of any other `KSampler` is `refining`, with the real `value` and `max`. The phases come from the graph,
  so they hold for every workflow file whatever its node numbers. A `progress` message without valid counts reports
  nothing.
- Messages of other prompts, binary frames and frames that are not the documented JSON are skipped. The outcome rests on
  the closing message: `execution_success`, `execution_error` or `execution_interrupted`.
- The history entry may appear only after `execution_success`: `fake_comfy` records it after the message, and the
  order in ComfyUI `v0.43.0` has not been read. The adapter asks `GET /history/{prompt_id}` again for up to 5 s before
  it reports the entry missing.
- `download(ImageOutput)` returns the bytes of `GET /view`, refusing an empty file and anything over 64 MiB.
  Validating the image is the persistence stage's job.
- `interrupt` sends `POST /interrupt` with no body and returns whether ComfyUI answered 200.
- `info` returns `system.comfyui_version` and the devices of `GET /system_stats`. Comparing the version with the pin is
  the caller's job.
- The adapter calls only the six paths in its `ENDPOINTS`, all from [Runtime contracts](runtime-contracts.md#endpoints-1).
  The HTTP client ignores proxy settings and follows no redirect; the WebSocket client ignores proxy settings and sends
  no keepalive pings, because the stall timeout already detects a silent runtime.

Timeouts are the [GPU orchestration](gpu-orchestration.md#timeouts) defaults: 300 s in total for a render, and 120 s
without a message of the render's own prompt. Past either one, the adapter sends `POST /interrupt`, then raises. Other
calls, and opening the WebSocket, wait 10 s, the adapter's own value.

| Failure | Error | `code` |
| --- | --- | --- |
| No answer, refused connection, failed handshake, undocumented status | `ImageRuntimeUnavailableError` | `RUNTIME_UNAVAILABLE` |
| The WebSocket ends before the closing message, as when the runtime crashes | `ImageConnectionLostError`, a subclass of the above | `RUNTIME_UNAVAILABLE` |
| Invalid JSON, missing fields, no history entry or output image, a file empty or too large | `ImageResponseInvalidError`, a subclass of the above | `RUNTIME_UNAVAILABLE` |
| `execution_error` whose exception names `OutOfMemoryError`, or an error answer that does | `ImageOutOfMemoryError` | `IMAGE_OOM` |
| `POST /prompt` answers 400 with `error` and `node_errors` | `WorkflowRejectedError`, with the node identifiers | `INTERNAL` |
| Any other `execution_error` | `RenderFailedError` | `INTERNAL` |
| No message of the render within the stall timeout | `RenderStalledError` | `GENERATION_STALLED` |
| Past the total timeout | `RenderTimeoutError` | `GENERATION_TIMEOUT` |

A refused workflow and a node failure other than memory map to `INTERNAL`, because Lyrveil submits only its own files
and a retry of the same graph would fail the same way. Error messages hold paths, status codes, node identifiers and
exception types, never prompt text.

`tests/unit/test_comfyui_runtime.py` runs the adapter against `fake_comfy` over real HTTP and WebSocket: a render with
its progress and download, an interrupt, out of memory in the render and in the submission, a crash, a refused
workflow, a stall and the total timeout. It holds T-IMG-05 for both passes of the documented **Alta** graph, and it
reads the contract page and fails when the adapter's module names a path missing from the ComfyUI endpoint table.

#### Workflow templates

Delivered in P05.2b: the four versioned files of [Workflow](image-generation.md#workflow) in `workflows/comfyui/`, and
`images/workflows.py`, which fills them. `build_workflow(WorkflowSpec)` loads the file for the preset and the tiled
flag, then sets typed values on the parsed JSON: `ckpt_name`, `stop_at_clip_layer`, both prompt texts, the first-pass
size, and seed, steps, CFG, sampler and scheduler. The seed and CFG also go to the second pass of **Alta**, which
takes its own steps and `denoise`. Each node is checked for its class before it is set, and a value outside the
documented range raises `WorkflowError`. `final_size` gives the saved size: the first-pass size, times 1.5 for **Alta**,
a multiple of 8.

The sampler name is checked for shape only, not against ComfyUI's `KSAMPLER_NAMES`; ComfyUI refuses an unknown name
at validation. The backend and test images copy `workflows/` to `/app/workflows/comfyui`. The `render` stage of
`image_request` calls the builder ([Image job](#image-job)).

#### Visual consistency

Delivered in P05.3b: `images/visual_consistency.py` holds `ImageScene` and `VisualConsistencyService`. `build(visual,
age, scene, preset)` returns `FinalPrompt`: the positive and negative prompts in the order of
[Final prompt](image-generation.md#final-prompt), the identity segment, the outfit used, the token estimate and whether
the identity ends in the first chunk. `filter_scene` applies the [scene filter](image-generation.md#scene-filter) and
lists every dropped tag with the step that dropped it. Both are pure and read no setting.

`services/visual_profile_translation.py` holds `translate_visual_profile`: it generates `VisualIdentityResult`
(`prompts/visual_profile.py`) under `identity_grammar`, a grammar whose slot values and outfit keys come from the
vocabulary and the card, merges the result with `apply_identity_result`, which keeps the locked slots, and validates the
merged card with `validate_card`, with one repair. `prompts/gbnf.py` gained `json_schema_to_gbnf` for such narrowed
schemas. Not delivered: the `visual_profile_translate` handler, the save path that queues it, and the write of the
translated slots. The builder is called by the `prompt` stage of `image_request` ([Image job](#image-job)).

#### Image prompt stage

Delivered in P05.4a: `services/image_prompt.py` holds `ImagePromptStage`, the work of the `prompt` stage of
[Job stages](image-generation.md#job-stages). `run(image, chat, genkey)` takes an `ImagePromptRequest` (card, request,
image preset, quality, requested aspect, the outfit picked in the form) and the image message's reply context, loaded
with `load_context_request` at the message's seq, or None outside a chat. It returns `PreparedImage`:

- `caption`: `write_caption` builds the chat's reply prompt with `ContextRequest.extra_line` set to
  `image_caption_line` and the band capped at 120, samples it with `reply_request` (the reply settings, shared with
  `ChatInferenceService`), and streams one generation through `TurnCutoff`, `ReplyNormalizer` and the reply checks,
  publishing nothing. `Caption.text` is None when the caption failed, and `failure` says why.
- `scene`: `generate_scene` runs `generate_structured` under `scene_grammar` (`prompts/image_scene.py`) and falls back
  to `fallback_scene` when the output and its repair are refused. `GeneratedScene.fallback` records it.
- `prompt`: `VisualConsistencyService.build` on the scene.
- `aspect`, `width`, `height`: `resolve_aspect` and `final_size` of `images/workflows.py`.

The caption call always comes before the scene call.

#### Image job

Delivered in P05.4b: `services/image_generation.py` holds `ImageGenerationService`, the `JobHandler` of `image_request`
(`Worker.image_runtime` and `Worker.images` give it its runtime and `ImageSettings`; without an image runtime the
worker registers no handler and such jobs wait). Each stage hands the next one the whole payload:

- `prompt` (llm): runs `ImagePromptStage` on the reply context of the job's message (`load_context_request` at its
  `seq`) and chooses the seed unless the request gave one. The payload then holds the caption and its checks, the scene,
  the final prompts, the outfit key, the aspect and the size.
- `render` (image): `build_workflow` with the quality preset and the profile's checkpoint; the second attempt uses the
  tiled file. Since P06.4 a second out of memory on **Alta** gets one tiled **Rapida** try (`render_retry`,
  [Failures](image-generation.md#failures)). `ComfyUiRuntime.render` publishes `image.progress`, and the file is staged
  at `.staging/<job id>/render` (`MediaStore.stage_job_file`, which `clear_staging` leaves alone).
- `persist` (none): Pillow validates the staged file (`prepare_upload`), the thumbnail and the original are written under
  names derived from the job id, then one transaction inserts `generated_images`, links the variant, writes the caption
  as the variant's text, sets the message `complete` (`kind` `mixed`, or `image` without a caption) and its
  `image_id`. Then `image.completed` and `message.completed`.

The message stays `queued` while the job works, so the startup rule that interrupts `streaming` messages never touches
it. A failure with no attempt left sets the message and the variant `failed` and publishes `message.failed`; a
`ContentPolicyBlockedError` is `CONTENT_POLICY_BLOCKED`, an outfit the card does not offer is `VALIDATION_ERROR`, and an
`ImageRuntimeError` keeps its port code, retried only for out of memory, an unavailable runtime, a stall or a timeout.

`services/images.py` holds `ImageService`: `request` saves the user's request, the image placeholder and the job in one
transaction (a full queue rolls all of it back), `regenerate` adds a `generated` variant and queues the job at the
`render` stage with the stored prompt (`JobQueue.enqueue(..., stage=)`), `get` and `media_file` read the metadata and
the files. No setting store chooses the profile or the quality yet. Since P05.7 the worker runs the jobs, and its
scheduler asks the orchestrator for the image mode ([Delivered in P05.7](image-generation.md#delivered-in-p057)).

Delivered in P05.5a ([Modes](image-generation.md#delivered-in-p055a)):

- `images/request_detector.py` holds the pure `detect_image_request(text, language)`; its word lists are under
  `prompts/<code>/image_request_*.txt`.
- `MessageService(engine, ..., images=ImageService)` sends a detected or `intent: image` message through
  `ImageService.request` (with `client_message_id` replay). `persistence/settings.py` holds the keys
  `images.auto_detect_requests` (default on) and `images.contextual_enabled` (default off).
- `ImageService.request` takes `steps` and `cfg` (`_sampling` in `ImageGenerationService` falls back to the settings),
  `request_asset` queues a job with no conversation or message (character mode), and `queue_proposed` queues the image
  of an accepted proposal inside the caller's transaction.
- The `persist` stage links a character-mode image as a `character_assets` row (`persistence.assets.link_generated`)
  and publishes `image.completed` with `message_id: null` when there is no message.
- `services/image_proposals.py` holds `ImageProposalService` (`queue_if_due`, `propose`, `accept`, `reject`,
  `expire_stale`) on `persistence/image_proposals.py` and the `image_proposals` table (revision `0014`). The
  `image_proposal` job handler is not written, so `main()` registers none and nothing calls `queue_if_due` yet.

### Prompt formatter

Delivered in P02.3b: `prompts/formatter.py`, the [Prompt format](prompting.md#prompt-format) of each family.
Since P02.5c `ChatInferenceService` renders replies with it; since P04.5 `ContextBuilder` fills the parts.

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
It holds back only what a later character can still change: the start of the reply while it may be a speaker label
or a wrapping quote (up to the first 40 characters), the start of a line that may be Markdown, an asterisk span that
may be removed, and whitespace that may be trailing. The variant text is written to
the database every 2 seconds, and at the end. A corrected check that fires publishes `message.restarted`,
and the second generation overwrites the same variant. After a crash the reply keeps what was
written, its status becomes `interrupted` and the interface offers regeneration.

Delivered in P02.5c, in `services/chat_inference.py`, with the event side in `events/hub.py`
([APIs](api.md#event-websocket)):

| Moment | Database | Events |
| --- | --- | --- |
| Run starts | Message `streaming`, text emptied; the payload's variant stays `streaming` | `typing.started` |
| Each token | The text the normalizer released, written every 2 s (`FLUSH_INTERVAL_S`), each time in its own short transaction | `message.delta` with the released text, when there is any |
| Runtime finishes | Variant and message `complete` with the whole normalized text, and the variant's `checks_json`; `interrupted` when the runtime reports `aborted` | The held text as a last `message.delta`, `typing.stopped`, `message.completed` |
| Failure, retry coming | Message back to `queued`, text emptied, so the next run starts clean | `typing.stopped`, `message.updated` |
| Final failure | Variant and message `failed`, with the text streamed so far | `typing.stopped`, `message.failed` |
| Shutdown cancels the run | Text flushed, reply left `streaming` | None |
| The user cancels the run (P06.3) | Variant and message `interrupted` with the text released so far; the scheduler then aborts the generation and ends the job `cancelled` | `typing.stopped`, `message.completed` |
| Next start | Every `streaming` message and its active variant become `interrupted`; their running job ends `failed` with `JOB_INTERRUPTED` | None |

- The handler decides "retry coming" with the queue's own rule: a retryable failure (a `TextRuntimeError`, a lost lease,
  or a `JobFailedError` marked retryable) while `attempt` is below the kind's maximum. Any other exception is a final
  `INTERNAL` failure. A preemption always counts as a retry.
- A cancellation during a run is a lost lease (`LEASE_LOST`), unless the lifespan has called `close()` first, which
  marks the shutdown, or `RunContext.stop` names the user's cancellation or a preemption (P06.3). A reply job cancelled
  while its reply was still `queued` loses its placeholder through `cancelled` (`CancellingHandler`).
- A run whose variant is no longer waiting (finished by a run whose job state a restart lost, replaced, or deleted) is
  done at once with `{"skipped": true}`, without touching the runtime. A reply whose chat was deleted ends `failed`.
- Since P04.5 the prompt is built by `ContextBuilder` ([Delivered in P04.5](memory-engine.md#delivered-in-p045)) from
  `load_context_request`: the whole card, the chat's `complete` and `interrupted` messages with text before the reply
  (at most the newest 100), the newest user message among them as the current one, the scenario copy and the active
  summary checkpoints, all in one read transaction. The request sends the band's `max_length` and the context size the
  runtime reports. After the reply is announced, the memories of block 5 get their use recorded
  (`FtsMemoryRetriever.record_use`, its own transaction; a failure is logged and leaves the reply complete), and the
  selection is kept in memory as the chat's `previous_ids` for its next reply. The assembly is logged as
  `context_assembled`, counts only, and `context_over_budget` when even the parts never removed exceed the window.
  Until P04.5 the prompt was `context/minimal.py`, blocks 1 and 2 and the newest turns trimmed a quarter at a time.
- `ReplySettings` holds the documented defaults while no settings store exists: the `gemma4` family, the `gemma4` chat
  sampler preset, `max_length` 400 (`llm.max_length`, the cap of every length band) and the user `Utente`. Both tiers'
  default text profiles are `gemma4`, so another active profile is not followed yet. `Worker.context` carries the
  `context.*` budgets (`ContextSettings`).
- Choices the documents did not fix: a crash or a shutdown in the middle of a reply ends its job at the next start
  instead of queuing it again, so the partial text the user saw is not silently replaced; a reply still `queued` when
  the backend stops is queued again. With no user message left before the reply (the user deleted it) the run fails
  with `INTERNAL`.
- Since P04.8a every token goes through the card's `ReplyNormalizer` in `ChatInferenceService._stream`, the one path a
  generated reply text takes; the corrective generation of P04.8b goes through it again. Deltas, flushes, the failure text and
  the stored reply all hold only released text, so the client, the database and later prompts never see a label,
  Markdown or a removed span. Text still held back when a run fails or is cancelled is dropped. When N4 cuts a reply
  that the length limit stopped, the words after the cut were already shown: `message.completed` carries the delivered
  text, which replaces them ([Reply normalization](consistency-engine.md#delivered-in-p048a)).
- Since P04.8b the V2 and V3 cut runs before the normalizer, and each sentence the normalizer releases is checked
  ([Delivered in P04.8b](consistency-engine.md#delivered-in-p048b)). A run makes at most three generations under the
  same lease and `genkey`, each through `_stream` with a new normalizer:

  | Moment | Database | Events |
  | --- | --- | --- |
  | V6, V10 or V11 in the first generation | The runtime is asked to abort and the rest of its stream is dropped; text emptied | `message.restarted` with the check, then the corrective generation's deltas from offset 0 |
  | V2 or V3 | The reply ends at the cut, and the runtime is asked to abort | None of its own |
  | Empty reply (V1), first time | Nothing was shown; the same prompt runs again | None |
  | Empty reply again | Variant and message `failed` with empty text and their `checks_json`; job `REPLY_EMPTY`, not retried | `typing.stopped`, `message.updated`, `message.failed` |
  | V4 in any generation | The runtime is asked to abort; variant and message `failed` with the fixed notice; job `CONTENT_POLICY_BLOCKED`, not retried | `message.restarted`, `typing.stopped`, `message.updated` with the notice, `message.failed` |

  The finish reason `aborted` marks a reply `interrupted` only when someone else aborted it, not when this run did.
  The adaptive notes are read by `load_context_request` from the `checks_json` of the chat's last 4 replies.

`tests/integration/test_reply_streaming.py` holds T-MSG-03 (token order, the flush times against a fake clock, the
stored text), T-MSG-09, T-MSG-12 through the application's `/ws` with its lifespan worker, T-MSG-13 through a restart
of `main()`, the retry path, T-SEC-06, a reply normalized before it is shown or stored, with its `checks_json`, and
T-CON-10 with the V1 and V4 endings.
`tests/unit/test_event_hub.py` covers the replay rules and the bounded queue.

### Startup sequence

1. Load settings, configure logging.
2. Open the database and verify capabilities.
3. Back up if migrations are pending, then migrate.
4. Remove unfinished files from `data/media/.staging` and orphan media.
5. Requeue `running` jobs, mark streaming variants `interrupted`.
6. Fetch missed lifecycle events, start the state long poll.
7. Start the WebSocket hub, job worker and proactive evaluator.
8. Report ready.

Delivered so far: steps 1 and 2, step 3 without the backup, which is still planned, step 5 ([Job queue](#job-queue), [Streaming persistence](#streaming-persistence)),
and step 7 for the WebSocket hub, built with the application, and the job worker, started by its lifespan. The same
lifespan runs the daily backup (P03.7b, `services/daily_backup.py`, [Backup and
restore](operations.md#backup-and-restore)), which first removes what a backup cut by a stop left in `backups/`. Step 4
is delivered for the staging folder only (P03.4a, `MediaStore.clear_staging`, run after step 5; a failure is logged as
`media_staging_cleanup_failed` and does not stop startup); orphan media cleanup, step 6 and the proactive evaluator of
step 7 are planned. Step 6 is not delivered as a long poll or an event replay. Since P05.7 the job worker reads
`GET /v1/state` on each pass, except when nothing queued needs the GPU and it has already seen or asked for `llm`, and
it asks for the mode its queue needs. A lease the orchestrator refuses
(`NOT_READY`) keeps a job queued. In step 5 the replies are recovered before the other jobs,
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
| `visual_profile_translate` | Lease, structured generation of `VisualIdentityResult` under a vocabulary grammar, sanitizer, store the identity slots and wardrobe tags that are not locked. The generation and the merge exist since P05.3b ([Visual consistency](#visual-consistency)); the handler does not. |
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

Delivered in P04.2a: `memory_extraction`, run by `MemoryExtractionService` (`services/memory_extraction.py`) and
registered in the lifespan beside the others. `ChatInferenceService` runs the extraction trigger after a reply is
stored and announced, in its own transaction, and logs a failure of the trigger without failing the reply. The flow,
the window, the idempotency key and what is applied are in [Memory engine](memory-engine.md#extraction-pipeline).
Unlike the draft, `STRUCTURED_OUTPUT_INVALID` is retryable here: the job retries after 1 and 5 minutes. The job's
`result_json` holds `status` and, for a completed run, the run id, its window and the counts of what was added,
confirmed, superseded, made uncertain, dropped and deferred.

Delivered in P04.2b: the consistency rules step. `MemoryConsistencyService` (`services/memory_consistency.py`) applies
each memory item in the run's commit transaction, with the pure rules of `memory/consistency.py`, and expires temporary
memories when a run starts. The lifespan passes the event hub to `MemoryExtractionService`, which publishes
`memory.conflict` after the commit. See [Delivered in P04.2b](memory-engine.md#delivered-in-p042b).

Delivered in P04.4: `summary_checkpoint`, run by `SummaryService` (`services/summaries.py`) and registered in the
lifespan beside the others. `ChatInferenceService` checks the summary trigger beside the extraction trigger, in the same
transaction after the reply is stored; a finished run checks it again for what is left. A run does one step: a range
named by the payload, the next chunk, or the next rollup. `STRUCTURED_OUTPUT_INVALID` is retryable, with the kind's
backoff. The job's `result_json` holds `status` and, for a completed run, the checkpoint id, `kind`, level, range,
version, token count and attempts. See [Delivered in P04.4](memory-engine.md#delivered-in-p044).

Delivered in P04.2c: guard 6 in `MemoryConsistencyService.apply` (an item a tombstone stops is counted in the job result
as `blocked_items` and writes nothing), and `MemoryService` (`services/memories.py`, SQL in `persistence/memories.py`)
with `edit` and `delete` as single write transactions. No HTTP route calls them yet (`PATCH` and `DELETE
/memories/{id}` come with P04.9). See [Delivered in P04.2c](memory-engine.md#delivered-in-p042c).

Delivered in P04.7: `memory_reevaluation`, run by `MemoryExtractionService` and registered in the lifespan beside
`memory_extraction`. Its payload `{"from_seq", "to_seq"}` names the range; the run reads it up to the cursor, never
moves the cursor, and its `result_json` adds `state_skipped` and `state_update_skipped`. `MessageService` invalidates
derived data through `InvalidationService` in the transaction of an edit, deletion, regeneration or variant switch, and
the edit, delete and activate routes wake the worker for the jobs it queues. A regenerated reply that extraction had
passed queues its re-evaluation from `ChatInferenceService`, in the transaction of the extraction trigger. See
[Delivered in P04.7](memory-engine.md#delivered-in-p047).

### Retry policy

| Failure | Attempts | Backoff |
| --- | --- | --- |
| `chat_reply`: runtime failure, `LEASE_LOST` | 2 | 5 s |
| `image_request`: render failure | 2, the second with tiled decode. Since P06.4 an out of memory of the tiled **Alta** try adds one **Rapida** try | 10 s |
| Structured output invalid | 2 within the same job, then the job retry policy | None |
| Extraction and summaries | 3 | 1 min, 5 min, 30 min |
| Preemption of background work | No attempt consumed | None |

### Job queue

Delivered in P02.4: revision `0002` creates `generation_jobs` ([Data model](data-model.md#generation_jobs)), and
`jobs/` holds the queue ([ADR 0010](decisions/0010-job-queue-and-scheduling-policy.md)). Since P02.5c the application's
lifespan runs the worker with `ChatInferenceService` for `chat_reply` and `chat_regenerate`, and a send or a
regeneration wakes it at once (`JobScheduler.notify`); jobs of other kinds wait. Since P05.7, with a `ModeSwitch`
(`mode_switch=`), the worker asks the orchestrator for the mode its queue needs ([Delivered in
P05.7](image-generation.md#delivered-in-p057)). Since P06.3 it decides by the full [Selection](gpu-orchestration.md#selection)
rules, preempts background work and stops cancelled jobs ([ADR 0039](decisions/0039-scheduling-clocks-batches-and-stops.md)).

| Module | Responsibility |
| --- | --- |
| `jobs/policy.py` | `JobKind`, `JobStatus`, `RequiredMode`, `JobClass`, and per kind the priority, class, maximum attempts, backoff and stages of [Job kinds and priorities](gpu-orchestration.md#job-kinds-and-priorities) |
| `jobs/queue.py` | `JobQueue`: every read and state change of `generation_jobs`, on a connection the caller owns |
| `jobs/handlers.py` | The handler contract: `RunContext` with its `stop` (`StopReason`), the outcomes `JobDone` and `NextStage`, `JobFailedError` (since P06.4 with `fallback`), `JobPreemptedError`, since P06.2 `AbandoningHandler` for a failure before the stage runs, and since P06.3 `CancellingHandler` for a cancelled job's placeholder |
| `jobs/selection.py` | Since P06.3: the pure selection rules (`decide`, `preempts`) and their settings (`SchedulingRules`) |
| `jobs/scheduler.py` | `JobScheduler`, the single worker, and `recover_interrupted_jobs` for startup step 5 |
| `services/job_control.py` | Since P06.3: `JobControlService.cancel` for the cancel route, and `remove_placeholder`, which the reply and image handlers share |

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
  that attempt; otherwise the job ends `failed`. Since P06.4 a retryable `JobFailedError` with `fallback`, a payload,
  queues the job again in its stage with that payload and the attempt count reset, after the same backoff, whatever
  attempts remain; the handler decides when its fallback is used up. `error_code` and `error_detail`, cut to 500 characters, keep the last
  reason in both cases. Any other exception ends the job at once with `INTERNAL`, because a bug does not heal on retry.
- **Restart.** At startup, before any worker, every `running` job is queued again at once in the stage it was running,
  with `JOB_INTERRUPTED` as its reason and its lease cleared. The interrupted run counts as an attempt, so a job that
  brings the process down cannot loop forever: one that was on its last attempt ends `failed` with `JOB_INTERRUPTED`.
- **VRAM wait.** Since P06.2, with a mode switch, the job that decides the mode fails with `GPU_INSUFFICIENT_VRAM` once
  the orchestrator has held its load back for 120 s, without its stage running ([Orchestrator package](#orchestrator-package)).
- **Selection with a mode switch.** Since P06.3 each pass groups the eligible jobs (foreground text, other text,
  images) and applies `selection.decide`: batches of images, the 60 s image wait, at most 4 images in a row while text
  work waits, and soft and background work after 20 s of user idleness. The clocks live in memory
  ([Selection](gpu-orchestration.md#selection)).
- **Preemption and cancellation.** Since P06.3 a running GPU stage is watched at each `notify()` and every second. A
  foreground job queued after a background generation started preempts it; a cancellation request stops any job. The
  handler is cancelled with `RunContext.stop` set, the runtime is aborted under the lease (`TextRuntime.abort(genkey)`
  or `ImageRuntime.interrupt()`, which `main()` passes to the scheduler), and the job is queued again with its attempt
  given back, or ends `cancelled` with `JOB_CANCELLED`. A queued job left with a cancellation request, by a restart or
  a run that ended just then, is ended at the next pass. `JobQueue` gained `eligible`, `cancel_queued`,
  `request_cancel`, `end_cancelled` and `next_cancel_pending`.

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
`domain/identifiers.py`, which writes UUIDv7 keys and UTC text with milliseconds. Since P06.3,
`tests/unit/test_scheduling_rules.py` checks the selection rules, and `tests/integration/test_job_cancellation.py`
the preemption, the cancellation of each image stage, of a running reply, and the cancel route.

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
runtime dependency, the library Uvicorn needs to upgrade `/ws`; without it Uvicorn refuses every handshake. Since
P04.1, snowballstemmer 3.1.1 is a runtime dependency, the stemmer of the memory index; its `uv.lock` entry was
generated with the lockfile command above. The test
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
`LLM_READY`, through a lease, and back to `IDLE` on `POST /v1/drain`. Since P06.1
`tests/integration/test_image_lifecycle.py` holds T-IMG-04, the 18 steps of an image request through the control API,
and `tests/unit/test_exclusivity_property.py` holds T-ORC-02, the property over random event sequences. A
`PUT /v1/desired-mode` keeps its `job_id`, which the events of the switch it causes carry. Since P06.4 the supervisor
classifies a failed text load as out of memory (`LoadFailedError.oom`) and has `await_ready` for a runtime recovery
finds starting, and the manager applies the whole [recovery table](gpu-orchestration.md#recovery), restarts a runtime
after an `oom` release, reports `load_failure`, returns to text after three failed image loads, and retries `ERROR`
on a timer. `tests/unit/test_recovery_table.py` holds T-ORC-06 and the orchestrator side of T-ORC-08.

On the backend side, `lyrveil/orchestration/client.py` holds `OrchestratorClient`, which
uses the standard library and no proxy. `acquire_lease` raises `LeaseRefusedError` with
`NOT_READY` or `LEASE_HELD`. `renew_lease` raises `LeaseLostError` on `410`.
`release_lease` returns `False` on `410`, when nothing is left to release. Since P05.7
`read_state` returns a `GpuStatus` (state, desired mode, drain flag, and the `ready_mode` derived from the state), and
`request_mode` calls `PUT /v1/desired-mode`, raising `ModeRefusedError` on `409 CONFLICT` while draining. Any other
failure raises `OrchestratorUnavailableError`. The client blocks, so callers run it in a
worker thread. `JobScheduler` is its caller, and heartbeats every 10 s ([Job queue](#job-queue)).
`tests/unit/test_orchestrator_client.py` tests it against a local HTTP server.

Since P06.1 `GpuStatus` also carries `seq`, `boot_id` and `blocked_reason` when the answer has them, and
`wait_for_state(since, wait_s)` long-polls `GET /v1/state?since=&wait=`, with `wait_s` held to 25 s and an answer
without `seq` or `boot_id` refused as unavailable. `lyrveil/orchestration/gpu_state.py` holds `GpuStateFollower`,
which runs beside the scheduler when `Worker.gpu_states` is set, as `main` does. It publishes
[`gpu.state`](api.md#event-websocket) once for each new `boot_id` and `seq`, with the state, the `label_key` of its
[interface label](gpu-orchestration.md#interface-labels), the queue depth (`JobQueue.depth`, queued and running jobs)
and the blocked reason. The first read, and the first after a failure, does not wait, since the orchestrator may have
restarted with a lower `seq`; a failure waits 5 s. `tests/unit/test_gpu_state.py` tests the label of every state and
the follower; `tests/integration/test_reply_streaming.py` checks that the event reaches the socket.

Since P06.2 the orchestrator holds a load back in `IDLE` when other programs leave too little free VRAM
([Verifying GPU release](gpu-orchestration.md#verifying-gpu-release)), and `GpuStatus.blocked_vram` carries the mode,
`free_mib` and `required_mib` of its last check. `JobScheduler` gives the job that decides the mode `vram_wait_s`, 120 s
by default (`vram.wait_seconds`, not read from `application_settings` yet), from the first pass that sees the hold for
that job. Then the job fails with `GPU_INSUFFICIENT_VRAM`, not retried, with an Italian `error_detail` that names the
two numbers, and the next job gets a wait of its own. The stage never runs: the job is marked running without a lease,
and a handler that shows its job to the user implements `AbandoningHandler.abandon`, which records the failure without
touching a runtime. `ChatInferenceService` sets the reply and its variant `failed` with no text, and
`ImageGenerationService` sets the image message `failed`. Both publish `message.failed`. The other kinds fail in the
queue only. ADR [0038](decisions/0038-vram-hold-in-the-orchestrator-wait-in-the-backend.md) records the split.
`tests/integration/test_image_mode_switch.py` and `tests/integration/test_reply_streaming.py` test it (T-ORC-05,
backend side). The orchestrator's `tests/unit/test_vram_guard.py` holds T-ORC-04 and the orchestrator side of T-ORC-05.

Since P06.4 ([ADR 0040](decisions/0040-load-failures-launch-levels-and-timed-recovery.md),
[Load failures](gpu-orchestration.md#load-failures-and-launch-levels)):

- `GpuStatus.load_failure` is a `LoadFailure` (`seq`, `mode`, `code`, `oom`, `failures`, `final`, `job_id`) when the
  state document carries a valid one.
- `lyrveil/orchestration/launch_levels.py` holds `LaunchLevels` and `level_values`. `main` builds it from the catalog
  the image ships (`/app/config/model-catalog.json`) over `/data/runtime/llm-launch.env`, as `Worker.launch_levels`; a
  catalog that does not load is logged and leaves the level as it is. `lower()` moves the file to the next level,
  `observe(status)` does so once for a text `load_failure` with `oom`, and `current()` reads the level.
- `GpuStateFollower` takes `on_state`, a blocking callable that receives each new state before it is published, in a
  worker thread; `main` passes `LaunchLevels.observe`. An exception it raises is logged.
- `JobScheduler` takes `launch_levels` and calls `lower()` after a run that ends with `LLM_OOM`, before it releases
  the lease with `oom`. It ends the queued image job that a final image `load_failure` names with `IMAGE_LOAD_FAILED`,
  not retried, through `AbandoningHandler.abandon`, before it decides anything else in that pass.
- Tests: `tests/unit/test_launch_levels.py`, `tests/integration/test_launch_levels.py` (T-ORC-08, backend side) and
  `tests/unit/test_orchestrator_client.py`.
