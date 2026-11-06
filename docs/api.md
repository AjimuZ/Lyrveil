# APIs

Status: accepted (2026-11-01). Implementation: planned, phases P01 to P07.

Lyrveil has three interfaces: the REST API, the event WebSocket and the internal
control API. The first two pass through `lyrveil-frontend`. The third is reachable
only on the `lyrveil-control` network.

## REST conventions

| Topic | Rule |
| --- | --- |
| Base path | `/api/v1` |
| Format | JSON. Writes must send `Content-Type: application/json`, except uploads. |
| Schema | FastAPI generates OpenAPI at `/api/v1/openapi.json`. The frontend generates its types from it. |
| Long work | Returns `202` with a job. Progress arrives on the WebSocket. |
| Idempotency | `POST .../messages` takes `client_message_id`. Retrying returns the original result. |
| Pagination | Cursor based: `?cursor=&limit=`, default limit 50, maximum 200. Response `{"items": [], "next_cursor": null}`. |
| Authentication | None. Protection comes from loopback binding and Host and Origin checks ([ADR 0017](decisions/0017-local-only-security-model.md)). |

### Errors

Errors use `application/problem+json`:

```json
{
  "type": "https://lyrveil.invalid/errors/validation-error",
  "title": "Dati non validi",
  "status": 422,
  "code": "VALIDATION_ERROR",
  "detail": "L'età deve essere almeno 18.",
  "errors": [{"field": "age", "code": "too_small"}]
}
```

| Code | HTTP | Meaning |
| --- | --- | --- |
| `VALIDATION_ERROR` | 422 | The request breaks a schema. |
| `NOT_FOUND` | 404 | No such resource. |
| `CONFLICT` | 409 | State does not allow the operation. |
| `PAYLOAD_TOO_LARGE` | 413 | Upload or message over the limit. |
| `UNSUPPORTED_MEDIA` | 415 | Upload is not PNG, JPEG or WebP, or its bytes disagree. |
| `CONTENT_POLICY_BLOCKED` | 422 | A safeguard refused the data ([ADR 0018](decisions/0018-adult-only-characters-and-content-safeguards.md)). |
| `IMPORT_INVALID` | 422 | Character file invalid or of an unsupported version. |
| `PROMPT_SYNTAX_REFUSED` | 422 | An edited image prompt holds `embedding:` or a weight outside 0.5 to 1.5 ([Image generation](image-generation.md#sanitizer)). |
| `FORBIDDEN_ORIGIN` | 403 | Host or Origin not allowed. |
| `ORCHESTRATOR_UNAVAILABLE` | 503 | The control API does not answer. |
| `MODEL_PROFILE_UNAVAILABLE` | 409 | The profile does not exist, needs a larger hardware tier, or its file is missing. `detail` names the download command. |
| `INTERNAL` | 500 | Unexpected failure. Details stay in the logs. |

Job errors, reported through `job.updated`:

| Code | Meaning |
| --- | --- |
| `GPU_INSUFFICIENT_VRAM` | Other programs hold too much VRAM. |
| `LLM_LOAD_FAILED`, `IMAGE_LOAD_FAILED` | A runtime did not reach readiness. |
| `MODEL_FILE_MISSING` | The active profile's file is absent or has the wrong size. Checked before any mode change. |
| `LLM_OOM`, `IMAGE_OOM` | CUDA out of memory. |
| `GENERATION_TIMEOUT`, `GENERATION_STALLED` | Over the total time, or no progress. |
| `STRUCTURED_OUTPUT_INVALID` | JSON failed validation after all attempts. |
| `RUNTIME_UNAVAILABLE` | Runtime stopped responding. |
| `LEASE_LOST` | The lease expired or the orchestrator restarted. |
| `JOB_CANCELLED` | The user cancelled. |

Operational codes, reported in logs, in `last_error` of the state document and in **Risorse AI**:

| Code | Meaning |
| --- | --- |
| `DB_CAPABILITY_MISSING` | Startup check of FTS5 or WAL failed. |
| `EXCLUSIVITY_VIOLATION` | Reconciliation found both runtimes running. |
| `RUNTIME_CONTAINER_MISSING` | A runtime container does not exist. Run `.\setup.cmd`. |
| `RUNTIME_CONFIG_INVALID` | A runtime exited with status 78 because of its launch file. |
| `DOCKER_UNAVAILABLE` | The Docker Engine does not answer. |

## REST endpoints

All paths are under `/api/v1`.

### System

| Method and path | Purpose |
| --- | --- |
| `GET /health/live` | Process alive. Used by the container health check. |
| `GET /health/ready` | Database, migrations and FTS5 are fine. Reports the control API separately. |
| `GET /system/status` | GPU state, queue depth, runtime status, degraded flags. |
| `GET /system/resources` | VRAM, memory seen by Docker, disk, tokens per second. Real values only. |
| `GET /system/lifecycle-events?cursor=` | Transition log. |
| `POST /system/recover` | Asks the orchestrator to leave `ERROR`. |
| `GET /system/preflight` | The same checks as `start.cmd`, for the settings screen. |

### Characters

| Method and path | Purpose |
| --- | --- |
| `GET /characters?status=&q=` | List. |
| `POST /characters` | Create. Version 1. |
| `GET /characters/{id}` | Full card. |
| `PUT /characters/{id}` | Save. Creates a new version when anything changed. |
| `POST /characters/{id}/duplicate` | Copy of the card, with no conversations or memories. |
| `POST /characters/{id}/archive`, `/unarchive` | Hide or show. |
| `GET /characters/{id}/purge-preview` | Counts of what a purge removes. |
| `POST /characters/{id}/purge` | Body `{"confirm_name": "..."}` must match the first name. |
| `GET /characters/{id}/versions` | Version list. |
| `GET /characters/{id}/versions/{n}` | One snapshot. |
| `GET /characters/{id}/versions/{a}/diff/{b}` | Field-level comparison. |
| `POST /characters/{id}/versions/{n}/restore` | New version with that content. |
| `GET /characters/{id}/export?include_assets=` | Format in [Character system](character-system.md#exchange-format). |
| `POST /characters/import` | Upload a file. Always creates a new character. |
| `GET /characters/format-schema` | JSON Schema of the exchange format. |
| `POST /characters/drafts` | Body `{"description": "..."}`. Starts `character_draft`. The job result is an unsaved draft. |

### Assets

| Method and path | Purpose |
| --- | --- |
| `GET /characters/{id}/assets?kind=` | List. |
| `POST /characters/{id}/assets` | Multipart upload. Kinds `avatar`, `profile`, `reference`. |
| `POST /characters/{id}/assets/generate` | Body `{"purpose": "avatar", "scene": "", "aspect": "square", "outfit": null, "seed": null}`. Starts `image_request`. `aspect` and `outfit` take the same values as a chat image request. |
| `POST /characters/{id}/assets/{asset_id}/crop` | Body `{"x":0,"y":0,"width":512,"height":512}`. Saves a new variant. |
| `POST /characters/{id}/assets/{asset_id}/primary` | Sets the primary avatar. |
| `DELETE /characters/{id}/assets/{asset_id}` | Removes the file. |

### Scenarios

`GET`, `POST /scenarios`. `GET`, `PUT /scenarios/{id}`. `POST /scenarios/{id}/archive`.

### Conversations

| Method and path | Purpose |
| --- | --- |
| `GET /conversations?q=&status=&character_id=` | Chat list, pinned first, then by activity. Each row holds avatar, name, last message, time, unread count, typing and status. |
| `POST /conversations` | Body `{"character_id": "...", "scenario_id": null, "title": null, "language": null, "shares_memory": false}`. |
| `GET /conversations/{id}` | Details and current state summary. |
| `PATCH /conversations/{id}` | Title, pinned, archived, language, `shares_memory`. |
| `DELETE /conversations/{id}` | Soft delete. |
| `POST /conversations/{id}/undelete` | Restores within 30 days. |
| `GET /conversations/{id}/purge-preview`, `POST /conversations/{id}/purge` | Permanent removal. Body `{"confirm": true, "keep_images": true}`. |
| `POST /conversations/{id}/read` | Body `{"up_to_seq": 42}`. |
| `GET /conversations/{id}/search?q=` | Search in one chat. |
| `GET /conversations/{id}/export?format=json\|md` | Export. |
| `GET`, `PATCH /conversations/{id}/proactive` | Proactive settings. |

### Messages

| Method and path | Purpose |
| --- | --- |
| `GET /conversations/{id}/messages?before_seq=&limit=` | History, newest page first. |
| `POST /conversations/{id}/messages` | Send. See the example below. |
| `PATCH /messages/{id}` | Edit. Adds a variant and triggers re-evaluation. |
| `DELETE /messages/{id}` | Soft delete. Triggers re-evaluation. |
| `POST /messages/{id}/regenerate` | Last character message only. Adds a variant. |
| `GET /messages/{id}/variants` | All versions. |
| `POST /messages/{id}/variants/{variant_id}/activate` | Last character message only. |
| `POST /messages/{id}/remember` | "Ricorda questo": runs extraction on this message. |
| `GET /messages/{id}/diagnostics` | Prompt assembly. `404` unless diagnostic mode is on. |

Send a message:

```http
POST /api/v1/conversations/0192f3a0-7b1e-7c3d-9a10-3f5e8f2a1c44/messages
Content-Type: application/json

{
  "client_message_id": "0192f3a1-11aa-7000-8000-000000000001",
  "content": "Ciao! Com'è andata oggi alla mappa del porto?",
  "intent": "auto"
}
```

`intent` is `auto`, `text` or `image`. `auto` runs the
[request detector](image-generation.md#manual-mode). The message limit is 8000 characters.

```json
{
  "message": {"id": "...", "seq": 57, "author": "user", "status": "complete", "read_state": "sent"},
  "reply": {"id": "...", "seq": 58, "author": "character", "kind": "text", "status": "queued"},
  "job": {"id": "...", "kind": "chat_reply", "status": "queued", "queue_position": 1}
}
```

### Images

| Method and path | Purpose |
| --- | --- |
| `POST /conversations/{id}/images` | Explicit request. Body `{"request": "...", "aspect": "auto", "outfit": null, "quality": null, "seed": null, "steps": null, "cfg": null}`. `aspect` is `auto`, `square`, `portrait`, `landscape`, `tall` or `wide`, and `auto` follows the scene's framing. `outfit` is null to let the scene choose, `usual`, or a key of the character's wardrobe. `quality` is `alta`, `rapida` or null for the setting `images.quality`. |
| `GET /images?character_id=&conversation_id=&purpose=&cursor=` | Gallery. |
| `GET /images/{id}` | Metadata. |
| `POST /images/{id}/regenerate` | Same prompt. New seed unless `keep_seed` is true. |
| `DELETE /images/{id}` | Removes files. |
| `POST /image-proposals/{id}/accept`, `/reject` | Answer a contextual proposal. |

Files are served outside the JSON API:

| Path | Content |
| --- | --- |
| `GET /media/images/{id}/original` | Full image. |
| `GET /media/images/{id}/thumbnail` | WebP, longest side 512 px. |
| `GET /media/assets/{id}` | Character asset. |

Media is looked up by identifier. A request path never becomes a file path.

### Memory

| Method and path | Purpose |
| --- | --- |
| `GET /memories?character_id=&conversation_id=&scope=&kind=&provenance=&status=&locked=&q=&cursor=` | Search and filter. `character_id` is required. |
| `POST /memories` | Manual memory. Provenance `user_confirmed`. |
| `GET /memories/{id}` | Details with sources and conflict group. |
| `PATCH /memories/{id}` | Correct. Records a revision. |
| `DELETE /memories/{id}` | Deletes and adds a tombstone. |
| `POST /memories/{id}/lock`, `/unlock` | Protect from automation. |
| `POST /memories/{id}/confirm` | Provenance becomes `user_confirmed`. |
| `POST /memories/{id}/resolve` | Body `{"winner_id": "...", "note": ""}`. Resolves a conflict group. |
| `POST /memories/{id}/share`, `/unshare` | Copies to or removes from the shared scope. |
| `POST /memories/{id}/apply-to-profile` | For `identity_candidate`. Opens a new character version. |
| `GET /memories/{id}/revisions` | History. |
| `GET /episodes?character_id=&conversation_id=&q=` | Events. |
| `PATCH`, `DELETE /episodes/{id}` | Correct or remove. |
| `GET /conversations/{id}/summaries` | All checkpoints with status. |
| `GET /conversations/{id}/relationship` | Current state. |
| `PATCH /conversations/{id}/relationship` | Correct the narrative, expectations or limits. Records `user_correction`. |
| `GET /conversations/{id}/relationship/history` | Change log with reasons. |
| `POST /conversations/{id}/memory/reprocess` | Re-runs extraction on the whole conversation. |

### Jobs, scheduling, notifications, settings, data

| Method and path | Purpose |
| --- | --- |
| `GET /jobs?status=&kind=`, `GET /jobs/{id}` | Queue and details. |
| `POST /jobs/{id}/cancel`, `/retry` | Control. |
| `GET`, `POST /scheduled-events`, `PATCH`, `DELETE /scheduled-events/{id}` | Planned messages. |
| `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all` | In-app notifications. |
| `GET`, `PATCH /settings` | Application settings. |
| `GET /settings/models` | Catalog profiles and registered custom profiles, each with tier, license, size and local state (`verified`, `size_mismatch`, `missing`), the active profiles, the quality preset and the launch values. |
| `PATCH /settings/models` | Changes the active profiles, the quality preset and runtime values. A text profile change is applied at the next load, or at once through `/settings/models/apply`. Refused with `MODEL_PROFILE_UNAVAILABLE`. |
| `POST /settings/models/apply` | Reloads the text model with the current launch file. Waits for the running reply. Returns `202` with the job identifier. |
| `POST /settings/models/custom` | Body `{"kind": "text", "file": "name.gguf", "template": "gemma4"}`. The file must already be in its models folder. Starts the hash job and returns `202`. |
| `DELETE /settings/models/custom/{id}` | Removes the registration, never the file. Refused with `CONFLICT` while the profile is active. |
| `POST /backups`, `GET /backups` | Create and list. Restore is `scripts\lyrveil.cmd restore`, because it stops services. |

## Event WebSocket

Endpoint: `/ws`. One connection per browser tab. The server checks `Origin` and `Host`
before accepting.

Server frames:

```json
{"v": 1, "id": 1842, "type": "message.delta", "ts": "2026-11-01T18:00:00.123Z", "data": {}}
```

`id` grows for the life of the backend process. The server keeps the newest 2000 events
in memory. A client that reconnects sends:

```json
{"type": "hello", "last_event_id": 1842, "boot_id": "..."}
```

The server replays the missing events, or sends `resync.required` when the gap is too
old or the backend restarted. The client then reloads through REST. REST is always the
source of truth, so an event can be lost without any data loss.

| Type | Data | When |
| --- | --- | --- |
| `gpu.state` | `state`, `label_key`, `queue`, `blocked_reason` | Every transition. |
| `job.updated` | `job_id`, `kind`, `status`, `stage`, `queue_position`, `error_code` | Every job change. |
| `typing.started`, `typing.stopped` | `conversation_id`, `character_id` | Real generation start and end. |
| `message.created` | `message` | New message or placeholder. |
| `message.delta` | `message_id`, `variant_id`, `offset`, `text` | Streaming text, after the [reply normalizer](consistency-engine.md#reply-normalization). |
| `message.restarted` | `message_id`, `variant_id`, `check` | A [corrected check](consistency-engine.md#corrected) stopped the reply. The client clears the streamed text, and new deltas start again from offset 0. |
| `message.completed` | `message` | Reply finished. |
| `message.failed` | `message_id`, `error_code` | Reply failed. |
| `message.updated`, `message.deleted` | `message` or `message_id` | Edit, variant change, deletion. |
| `message.read` | `conversation_id`, `up_to_seq` | Simulated read state. |
| `conversation.updated` | Chat list row | Any list-visible change. |
| `image.progress` | `job_id`, `message_id`, `phase`, `value`, `max` | Prompt, waiting, loading, sampling, saving. |
| `image.completed` | `image`, `message_id` | File stored and linked. |
| `image.proposed` | `proposal` | Contextual proposal ready. |
| `memory.updated` | `memory_id`, `action` | Memory changed. |
| `memory.conflict` | `memory_id`, `conversation_id` | Needs review. |
| `notification.created` | `notification` | In-app notification. |
| `resync.required` | None | Reload through REST. |

Client frames: `hello` and `ping`. All actions go through REST.

`message.delta` carries private text and is never logged.

## Control API

Between backend and orchestrator only. Internal network, port 8100. Every request
except `/v1/health` needs `Authorization: Bearer <LYRVEIL_CONTROL_TOKEN>`. Requests carry
only enums, identifiers and numbers, never user text.

| Method and path | Purpose | Result |
| --- | --- | --- |
| `GET /v1/health` | Process alive | `200` |
| `GET /v1/state?since=&wait=` | Current state. Long poll up to `wait` seconds (25 at most) until `seq` exceeds `since` | State document |
| `PUT /v1/desired-mode` | Body `{"mode": "llm"\|"image"\|"none", "job_id": "..."}`. Applied when no lease is active | `202` |
| `POST /v1/leases` | Body `{"mode": "llm", "job_id": "...", "ttl_s": 30}` | `201` lease, or `409 NOT_READY` / `409 LEASE_HELD` |
| `POST /v1/leases/{id}/heartbeat` | Extends the lease | `200`, or `410 LEASE_EXPIRED` |
| `DELETE /v1/leases/{id}` | Body `{"outcome": "ok"\|"oom"\|"runtime_error"}` | `200` |
| `POST /v1/recover` | `ERROR` to `RECOVERING` | `202`, or `409` |
| `POST /v1/drain` | Finishes, stops runtimes, refuses new modes. For shutdown | `202` |
| `POST /v1/resume` | Leaves drain | `202` |
| `GET /v1/events?boot_id=&after=` | Buffered transitions, newest 5000 | Events |
| `GET /v1/preflight` | GPU and Docker facts | Report |

State document:

```json
{
  "boot_id": "0192f3a0-0000-7000-8000-000000000000",
  "seq": 128,
  "state": "LLM_READY",
  "desired_mode": "llm",
  "lease": null,
  "blocked_reason": null,
  "vram": {"total_mib": 12282, "used_mib": 10400, "free_mib": 1882},
  "runtimes": {
    "llm": {"container": "running", "health": "healthy", "exit_code": null},
    "image": {"container": "exited", "health": null, "exit_code": 0}
  },
  "last_error": null
}
```
