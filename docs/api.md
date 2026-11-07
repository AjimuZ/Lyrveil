# APIs

Status: accepted (2026-11-01). Implementation: partial. Delivered: the backend's health routes, the committed OpenAPI document, the frontend's client of the event WebSocket ([evidence](verification/2026-11-06-api-types-and-socket.md)), the token check, health and preflight routes of the [control API](#control-api), the Host and Origin checks with the `FORBIDDEN_ORIGIN` answer and the generic `INTERNAL` answer, and the handshake of `/ws` with its `connected` frame, the conversation routes listed under [Conversations](#conversations), the message routes listed under [Messages](#messages), the asset routes and the asset media routes delivered under [Assets](#assets) and [Images](#images), with `PAYLOAD_TOO_LARGE` and `UNSUPPORTED_MEDIA`, and on `/ws` the `hello` with replay or `resync.required` and the reply events listed under [Event WebSocket](#event-websocket). The other events, the other error codes and the rest are planned, phases P01 to P07.

Lyrveil has three interfaces: the REST API, the event WebSocket and the internal
control API. The first two pass through `lyrveil-frontend`. The third is reachable
only on the `lyrveil-control` network.

## REST conventions

| Topic | Rule |
| --- | --- |
| Base path | `/api/v1` |
| Format | JSON. Writes must send `Content-Type: application/json`, except uploads. |
| Schema | FastAPI generates OpenAPI at `/api/v1/openapi.json`. The document is committed as `backend/openapi.json`, a backend test keeps it equal to the application's, and the frontend generates its types from the committed file ([Frontend](frontend.md#api-types)). |
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
| `REPLY_EMPTY` | A reply was empty again after its retry (check V1). Not retried; the interface offers regeneration. |
| `CONTENT_POLICY_BLOCKED` | A safeguard refused generated text: a draft after its repair, or a reply sentence (check V4). Not retried. |
| `RUNTIME_UNAVAILABLE` | Runtime stopped responding. |
| `LEASE_LOST` | The lease expired or the orchestrator restarted. |
| `JOB_CANCELLED` | The user cancelled. |
| `JOB_INTERRUPTED` | A restart found the job running. It is queued again, or fails when that was its last attempt. |
| `INTERNAL` | A job handler failed unexpectedly. The job is not retried. Details stay in the logs. |

Operational codes, reported in logs, in `last_error` of the state document and in **Risorse AI**:

| Code | Meaning |
| --- | --- |
| `DB_CAPABILITY_MISSING` | Startup check of FTS5 or WAL failed. |
| `EXCLUSIVITY_VIOLATION` | Reconciliation found both runtimes running. |
| `RUNTIME_CONTAINER_MISSING` | A runtime container does not exist. Run `.\setup.cmd`. |
| `RUNTIME_CONFIG_INVALID` | A runtime exited with status 78 because of its launch file. |
| `DOCKER_UNAVAILABLE` | The Docker Engine does not answer. |
| `GPU_RELEASE_UNVERIFIED` | After a stop, the runtime container did not end, or the GPU memory did not settle or could not be read. |

## REST endpoints

All paths are under `/api/v1`.

### System

| Method and path | Purpose |
| --- | --- |
| `GET /health/live` | Process alive. Used by the container health check. Answers `200` with `{"status": "alive"}`. |
| `GET /health/ready` | Database, migrations and FTS5 are fine. Reports the control API separately. Answers `200` when ready and `503` otherwise, with `status`, `checks` and `control_api` in both ([Backend](backend.md#database-and-health)). |
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
| `POST /characters/import` | Multipart upload of `file`. Always creates a new character. |
| `GET /characters/format-schema` | JSON Schema of the exchange format. |
| `POST /characters/demo` | Adds the demo characters not loaded yet (flag `is_demo`). Answers `201` with `{"created": [...], "skipped": [...]}`. Never overwrites a character. |
| `POST /characters/drafts` | Body `{"description": "..."}`. Starts `character_draft`. The job result is an unsaved draft. |

Delivered in P03.5: the three exchange routes. `GET .../export` answers the file as a download named after the first name
(`include_assets` is `false` by default); `POST /characters/import` takes the multipart field `file` (up to 64 MB, else
`413 PAYLOAD_TOO_LARGE`) and answers `201` with `{"character": {...summary}, "assets_imported": n, "assets_without_data":
n}`; `GET .../format-schema` answers the JSON Schema (draft 2020-12). A refused file answers `422 IMPORT_INVALID` with an
Italian `detail` and writes nothing; since P03.6a a card text that describes a minor answers `422
CONTENT_POLICY_BLOCKED` instead. The routes are not in `backend/openapi.json` yet, like the other character routes.

Delivered in P02.6a and P03.1a: `GET /characters` (`status` `active` or `archived`, default `active`; `q` keeps the names
that contain it, ignoring case; newest update first), `POST /characters` and `GET /characters/{id}`.

- `POST` takes the card of [Character system](character-system.md#card-schema) as
  `{"identity": {...}, "traits": [...], "communication": {...}, "visual": {...}}`. Only `identity.first_name` and
  `identity.age` are required. A flat body with the identity fields at the top level, as the minimal chat form sends it
  (`{"first_name", "last_name", "age", "grammatical_gender", "primary_language"}`), is accepted too, with the other
  sections beside them. It answers `201` with the summary of the character.
- `422 VALIDATION_ERROR` carries an Italian `detail` naming the field, never the value, for: an empty or over-long name,
  an age under 18 or over 120, a value outside a closed list (gender, language, `address_form`, `narration`, `emoji`...),
  an unknown field, more entries than a list allows, a slot tag missing from the tag vocabulary, a free tag of more than
  4 words, an outfit with the reserved key `usual` or `scene`, a repeated outfit key, and a `fixed` or `base_offset`
  seed policy without `base_seed`. Tags are sanitized before they are stored.
- Since P03.6a, `422 CONTENT_POLICY_BLOCKED` when a text of the card describes a minor
  ([Content safeguards](security.md#character-data)). Its `detail` names the first such field, such as "Il campo
  «identity.biography» parla di un minore. La scheda descrive solo adulti.", never the text, and nothing is written.
  `PUT`, duplicate, restore and import answer the same.
- `GET /characters/{id}` answers the summary fields plus `card`, the whole card as stored, or `404 NOT_FOUND`. A character
  made before the card existed reads with the default sections.

Delivered in P03.1b: `PUT /characters/{id}` and the four version routes.

- `PUT` takes the same body as `POST`, nested or flat, and answers `200` with the character as `GET` does. A card equal
  to the stored one writes nothing and keeps `current_version_no`; any other change replaces the card and appends a
  version, actor `user`, whose `change_summary` names the sections that changed (`Modificati: identità, aspetto`).
  The locks in the body are the new locks, so the user's own save can change a locked trait or field. It answers `404
  NOT_FOUND` for an unknown character and `422 VALIDATION_ERROR` or `CONTENT_POLICY_BLOCKED` for what `POST` refuses;
  nothing is written then.
- `GET /characters/{id}/versions` answers `{"items": [{"version_no", "change_summary", "actor", "created_at"}]}`, newest
  first. `GET .../versions/{n}` adds `card`, the snapshot.
- `GET .../versions/{a}/diff/{b}` answers `{"from_version_no", "to_version_no", "changes": [{"field", "before",
  "after"}]}`. `field` is a dotted path such as `identity.age` or `visual.identity_en.hair_color`, in card order. A list
  (traits, interests, examples, outfits) is one field and compares whole. Equal versions give an empty list.
- `POST .../versions/{n}/restore` always appends a version, actor `restore`, summary `Ripristinata la versione n`,
  holding the card of version `n`, and answers the character. Versions are never changed or removed. Since P03.6a an
  old card that the content guard refuses today answers `422 CONTENT_POLICY_BLOCKED` and appends nothing.
- The three routes on a version answer `404 NOT_FOUND` for an unknown character or version.
- Delivered in P03.2a: `GET /characters/vocabulary` answers `{"items": [{"tag", "category", "aliases_it"}]}`, the whole tag
  vocabulary of the card categories in file order. The Creator builds its chips and its Italian search from it. It is
  declared before `/characters/{id}`, so `vocabulary` is never read as an id.
- Automatic sources are not routes yet. The service applies a proposal with `CharacterService.apply_proposal`, which
  keeps locked traits and locked visual fields and records actor `proposal`. Every route of this table that this
  section does not describe as delivered (export, import and format schema) is planned. These routes are not in
  `backend/openapi.json` until it is next regenerated with Docker.

Delivered in P03.3: `POST /characters/drafts` ([Assisted creation](character-system.md#assisted-creation)).

- The body is `{"description": "...", "character_id": null}`; unknown fields are refused. It answers `202` with
  `{"id", "kind": "character_draft", "status": "queued", "queue_position"}` and writes nothing but the job. `422
  VALIDATION_ERROR` for a blank description or one over 2000 characters, `422 CONTENT_POLICY_BLOCKED` (since P03.6a)
  for a description that describes a minor, with no job queued, `404 NOT_FOUND` for an unknown `character_id`, `409
  CONFLICT` when the queue is full.
- The job's result, read with `GET /jobs/{id}`, is `{"card", "proposed_fields", "character_id", "attempts"}`. No
  character exists until the user saves `card` with `POST /characters`, or with `PUT /characters/{id}` for a draft over
  a character; that draft already holds the stored locks. A draft refused twice ends `failed` with
  `STRUCTURED_OUTPUT_INVALID` and an Italian `error_detail` that names the field, such as "L'età deve essere almeno 18.",
  or with `CONTENT_POLICY_BLOCKED` when the content guard refused the last attempt.

Delivered in P03.2b: duplicate, archive, unarchive and purge.

- `POST /characters/{id}/duplicate` answers `201` with the summary of a new active character that holds a copy of the
  current card, at version 1, actor `duplicate`, summary `Duplicato da <first name>`. Its first name is the original
  followed by ` (copia)`, cut to stay within 80 characters. Conversations, memories and older versions are not copied. An
  archived character can be duplicated; the copy is active. `404 NOT_FOUND` for an unknown character. Since P03.6a,
  `422 CONTENT_POLICY_BLOCKED` when the stored card holds text that the content guard refuses today.
- `POST /characters/{id}/archive` and `/unarchive` answer `200` with the summary, whose `status` is then `archived` or
  `active`. Repeating either changes nothing. Nothing is deleted. The conversation list leaves out the conversations of
  an archived character, unless it is asked for that character with `character_id`; a new conversation with an archived
  character is refused with `409 CONFLICT`, as before. `404 NOT_FOUND` for an unknown character.
- `GET /characters/{id}/purge-preview` answers `{"conversations", "messages", "versions"}`: the conversations in any
  status, their messages and the card versions that a purge deletes. Memories, images and assets join the preview when
  their tables exist.
- `POST /characters/{id}/purge` with `{"confirm_name": "..."}` answers `204` after it deletes, in one transaction, the
  jobs that name the character, its conversations or their messages, the message variants, messages, conversations,
  traits, asset rows, visual profile, versions and the character; the asset files are removed after the commit. The
  preview does not count assets yet. The name must equal the first name (surrounding spaces ignored,
  capitals kept), otherwise `422 VALIDATION_ERROR`. `409 CONFLICT` while one of those jobs is running. `404 NOT_FOUND`
  for an unknown character. Nothing is deleted in any refusal.

### Assets

| Method and path | Purpose |
| --- | --- |
| `GET /characters/{id}/assets?kind=` | List. |
| `POST /characters/{id}/assets` | Multipart upload. Kinds `avatar`, `profile`, `reference`. |
| `POST /characters/{id}/assets/generate` | Body `{"purpose": "avatar", "scene": "", "aspect": "square", "outfit": null, "seed": null}`. Starts `image_request`. `aspect` and `outfit` take the same values as a chat image request. |
| `POST /characters/{id}/assets/{asset_id}/crop` | Body `{"x":0,"y":0,"width":512,"height":512}`. Saves a new variant. |
| `POST /characters/{id}/assets/{asset_id}/primary` | Sets the primary avatar. |
| `DELETE /characters/{id}/assets/{asset_id}` | Removes the file. |

Delivered in P03.4a, all but `generate`, and not in `backend/openapi.json`:

- An asset answers `{"id", "character_id", "kind", "source", "media_type", "width", "height", "byte_size", "sha256",
  "crop", "is_primary", "created_at", "url", "thumbnail_url"}`; `url` and `thumbnail_url` are its media routes. `crop`
  is null, or `{"source_asset_id", "x", "y", "width", "height"}` for a crop.
- `GET .../assets` lists the live assets, newest first. `kind` is optional; an unknown kind is `422 VALIDATION_ERROR`.
- `POST .../assets` takes the multipart fields `file` and `kind` (`avatar` by default) and answers `201`. `413
  PAYLOAD_TOO_LARGE` over 15 MB or 8192 x 8192 pixels, `415 UNSUPPORTED_MEDIA` for bytes that are not PNG, JPEG or WebP,
  that do not decode, or whose declared type disagrees, `422` for another kind. The first avatar becomes primary.
- `POST .../crop` answers `201` with a new 512 x 512 asset of the same kind. `x` and `y` are at least 0, `width` and
  `height` at least 1; a rectangle that leaves the image is `422`, a non-square one is trimmed to its centred square.
- `POST .../primary` answers `200` with the asset; `422` for an asset that is not an avatar.
- `DELETE` answers `204` and removes the file and the thumbnail; `409 CONFLICT` for the primary avatar.
- Every route answers `404 NOT_FOUND` for an unknown character, and for an asset that is deleted or belongs to another
  character.

### Scenarios

`GET`, `POST /scenarios`. `GET`, `PUT /scenarios/{id}`. `POST /scenarios/{id}/archive`.

Delivered in P03.7a, not in `backend/openapi.json`, with two routes added to the list above: `POST
/scenarios/{id}/unarchive` and `DELETE /scenarios/{id}`.

- A scenario answers `{"id", "title", "description", "setting", "opening_message", "character_id", "language",
  "status", "created_at", "updated_at"}`. `character_id` null means any character; `language` null inherits;
  `status` is `active` or `archived`.
- `POST` answers `201` and `PUT` replaces every editable field (`title`, `description`, `setting`, `opening_message`,
  `character_id`, `language`; a field left out takes its default). The title is trimmed and must not be blank (`422
  VALIDATION_ERROR`); the limits are 200 characters for the title and 2000 for each other text. A text that describes a
  minor is `422 CONTENT_POLICY_BLOCKED` naming the field. An unknown `character_id` is `404`.
- `GET` takes `status` (default `active`), `character_id` (the scenarios for that character and those for any
  character) and `q` (every word must appear in the title, description, setting or opening message; case and accents
  ignored). Newest change first.
- `archive` and `unarchive` answer the scenario. `DELETE` answers `204` and removes it; the conversations that started
  from it keep their copy and lose only the link. Every route answers `404` for an unknown scenario.

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

Delivered in P02.5a: `GET` and `POST /conversations`, `GET`, `PATCH` and `DELETE /conversations/{id}`, and `undelete`.
`GET` takes `status` (default `active`), `character_id` and `q`. A deleted chat answers `404`
everywhere except `undelete`, which answers `409` when the chat is not deleted or 30 days have passed. `POST` answers
`201`, `404` for an unknown character and `409` for an archived character. Since P03.7a a `scenario_id` is accepted: `404`
for an unknown scenario, `409` for an archived scenario or one made for another character. The conversation stores a
copy of the scenario, takes its language when the body gives none, and a non-empty opening message becomes its first
message, written by the character. A row also holds `scenario_id` (null once the scenario is deleted) and
`scenario_title` (from the copy). A character can have any number of conversations; `GET /conversations?character_id=`
lists them all. A row holds `avatar_url`, the thumbnail route of the character's primary avatar (null while it has none),
and lacks the last message and typing flag until messages exist. These routes are not
in `backend/openapi.json` until it is next regenerated with Docker.

Delivered in P02.5d: search. `q` (on the list) keeps the chats whose title or non-deleted messages contain every word
of it. `GET /conversations/{id}/search?q=&limit=` answers `{"items": [message, ...]}`, newest first, `limit` as for
history, `422` without `q` and `404` for a missing or deleted chat. Matching ignores case and accents ("perche" finds
"perché"; the FTS5 `unicode61 remove_diacritics 2` tokenizer). `q` is plain text: only its letters and digits are used,
each as a quoted FTS5 string, so `AND`, `OR`, `NOT`, `NEAR`, quotes, parentheses and `*` are ordinary words and never
raise or change the query. A `q` with no letter or digit finds nothing; a blank `q` on the list filters nothing. No
stemming yet: a word matches only itself.

### Messages

| Method and path | Purpose |
| --- | --- |
| `GET /conversations/{id}/messages?before_seq=&limit=` | History, newest page first. |
| `POST /conversations/{id}/messages` | Send. See the example below. |
| `PATCH /messages/{id}` | Edit. Adds a variant, invalidates derived data and triggers re-evaluation. |
| `DELETE /messages/{id}` | Soft delete. Invalidates derived data and triggers re-evaluation. |
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

Delivered in P02.5b, all except `remember` and `diagnostics` (P04, P06):

- **Send** answers `202`. It saves the user message (`complete`, `read_state` `sent`) and the reply placeholder (`queued`,
  empty text, one `streaming` variant that the job fills) in one transaction, and queues the `chat_reply` job in a second
  one, so the text is never lost to a full queue. Sending the same `client_message_id` to the same chat again returns the
  first result and writes nothing; if the first attempt died before queuing its job, the retry queues it. A chat that is
  deleted answers `404`, an archived one `409`, blank text `422` and text over 8000 characters `413`. `queue_position`
  counts the queued and running jobs that run before the job, itself included. Since P05.5a the `image` intent, and an
  `auto` message of at most 1000 characters that the [request detector](image-generation.md#manual-mode) reads as a
  request (while `images.auto_detect_requests` is on), save the user's message and an image message (`kind` `image`) and
  queue an `image_request` job instead, in one transaction: a full queue answers `409` and saves nothing, and an `image`
  intent over 1000 characters answers `422`. `text` and every other message produce a text reply.
- **History** returns the newest `limit` messages below `before_seq` (default: the newest), oldest first inside the page,
  as `{"items": [], "next_before_seq": null}`. `next_before_seq` is the `before_seq` of the next older page and null at
  the start. Deleted messages are left out and their `seq` is not reused. Queued, streaming, failed and interrupted
  replies are listed with their status, which is how a reload recovers the chat.
- **Edit** (`PATCH`) adds an `edited` variant, makes it active and sets `edited_at`; the same text again changes nothing.
  It answers `409` for a system message or one still queued or streaming.
- **Delete** answers `204`, `404` when already deleted and `409` for a message still queued or streaming.
- **Derived data.** Since P04.7 an edit, a deletion, a regeneration and a variant switch invalidate, in the same
  transaction, the memories, episodes, state rows and summaries that rested on the message, and queue a
  `memory_reevaluation` job and the summaries to write again; the answer is unchanged
  ([Delivered in P04.7](memory-engine.md#delivered-in-p047)).
- **Regenerate** answers `202` with the message, the new `variant_id` and the `chat_regenerate` job. It applies only to
  the last visible message of an active chat when a character wrote it and it is not queued or streaming (`409`
  otherwise). The new `generated` variant is active at once, so the message is `queued` with empty text and the older
  variants stay as alternatives. If the queue is full the new variant and the message are marked `failed` and the call
  answers `409`; an older variant can then be activated.
- **Variants** lists every version in creation order. **Activate** has the same last-character-message rule, copies the
  variant's text and status to the message and refuses a variant that is still `streaming` (`409`).

Delivered in P04.9b (`api/diagnostics.py`, `services/diagnostics.py`): the diagnostic mode of the Memory Studio.

- `GET /messages/{id}/diagnostics` answers the prompt assembly stored for the reply: `message_id`, `conversation_id`,
  `variant_id`, `created_at`, `token_total`, `context_limit`, `blocks` (tokens per prompt block: `global`, `identity`,
  `communication`, `scenario`, `summary`, `history`, `state`, `memories`, `anchor`, `current`), `budgets`
  (`context_limit`, `reply_reserve`, `safety_margin`, `available`, `memory_target`, `summary_target`), `notes`,
  `reductions`, `gaps`, `history_count`, `current_truncated`, `memories` (`id`, `type`, `kind`, `status`, `label`, `score`,
  `token_cost`, `parts` and the memory's current `content`, null once it is deleted), `summaries` and `prompt_text`
  (null unless it was stored while the mode was on). It is the assembly of the message's active variant, or the newest
  one when that variant has none. `404` while diagnostic mode is off and for a reply with no stored assembly.
- `GET /conversations/{id}/diagnostics` answers `{"items": []}`: the replies of the chat that have a stored assembly,
  newest first, with `message_id`, `seq`, the start of the reply (`excerpt`), `token_total`, `context_limit` and
  `created_at`. `404` while the mode is off and for an unknown chat. This route is not in the table above: it gives the
  Studio its choice list.
- `GET /settings` answers `{"app.diagnostic_mode": false}` and `PATCH /settings` takes the same key. These two routes
  hold only the settings delivered so far; any other key is refused with `422`, and the rest of
  [Configuration](configuration.md#application-settings) arrives with the screens that need it.

These routes are not in `backend/openapi.json` until it is next regenerated with Docker.

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

Delivered in P03.4a: `GET /media/assets/{id}` and `GET /media/assets/{id}/thumbnail` (WebP, longest side 512 px), with
`Cache-Control: private, max-age=31536000, immutable`, since a file never changes once written. An id that names no
live asset, or whose stored path leaves the media root, answers `404 NOT_FOUND`.

Delivered in P05.4b: `GET /media/images/{id}/original` and `/thumbnail` with the same headers and the same `404`, and
`GET /images/{id}`, which answers the stored metadata (size, request, scene, final prompts, seed, sampler values,
profile, preset, workflow file and ComfyUI version, but not the submitted workflow) with `url` and `thumbnail_url`. The
files come from `data/media`, so they load with ComfyUI off. A message carries `image_id`, the image of its active
variant (null for a message with none), and `image.completed` carries the same metadata as `image`.

Delivered in P05.5a ([Delivered in P05.5a](image-generation.md#delivered-in-p055a)):

- `POST /conversations/{id}/images` answers `202` with `message`, `reply` and `job`, as a message send does. It accepts
  no field beyond the documented ones (`422`). `steps` is 10 to 50 and `cfg` 1 to 12. It answers `404` for a missing
  chat, `409` for an archived chat or a full queue, and `422` for a blank request, an unknown aspect or quality, a value
  out of range or an outfit the character does not have.
- `POST /characters/{id}/assets/generate` answers `202` with `{"job": {...}}` and takes `purpose` (`avatar`, `profile`,
  `scene` or `variant`), `scene`, `aspect`, `outfit`, `quality` and `seed`. It answers `404` for an unknown character,
  `409` for a full queue and `422` for another purpose, a blank `scene` with the `scene` purpose, or the values above.
- `POST /image-proposals/{id}/accept` answers `202` like a send (`message` and `reply` are the image placeholder), and
  `/reject` answers `200` with the proposal. Both answer `404` for an unknown proposal and `409` for one already
  answered or older than 24 hours. The `image.proposed` event carries `proposal` (`id`, `conversation_id`,
  `message_id`, `scene`, `reason`, `status`, `job_id`, `created_at`, `decided_at`), and `image.completed` carries
  `message_id: null` for an image of character mode.

Planned (P05.6): the gallery, regeneration and deletion routes. The service behind regeneration exists
(`ImageService.regenerate`). None of the image routes is in `backend/openapi.json` until it is next regenerated with
Docker.

### Memory

Delivered in P04.9a: `GET` and `POST /memories`, `GET`, `PATCH` and `DELETE /memories/{id}`, `lock`, `unlock`,
`confirm` and `revisions`, `GET /episodes`, and `GET /conversations/{id}/summaries`, `/relationship` and
`/relationship/history`. Not routed yet: `resolve`, `share`, `unshare`, `apply-to-profile`, `PATCH` and `DELETE
/episodes/{id}`, `PATCH /conversations/{id}/relationship` and `memory/reprocess`. Like the conversation routes, the
delivered ones stay out of `backend/openapi.json` until it is next regenerated with Docker.

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

What P04.9a delivers (`api/memories.py`, `services/memory_studio.py`, `persistence/memory_studio.py`):

- **List.** `GET /memories` answers `{"items": [], "next_cursor": null}`, the most recently changed first, 50 per page
  (`limit` up to 200). `status` can be repeated and no `status` lists every status; `kind`, `provenance`, `scope`,
  `conversation_id` and `locked` filter exactly; `q` is plain text matched by stem in every language the character's
  memories use, and text with no searchable word matches nothing. Each item has the memory's columns, `confidence`,
  `locked`, `needs_review`, `conflict_group_id`, `version` and `source_count`. `404` for an unknown character and `422`
  for a `cursor` the server did not give.
- **One memory.** `GET /memories/{id}` adds `sources` (message id, conversation, `seq`, author, the start of the text the
  memory read, and `valid`, false once the message was deleted or shows another variant) and `conflict`, the other
  memories of its conflict group. `POST /memories` takes `{"conversation_id", "content", "kind", "importance"}` and writes
  a lasting, active memory in the conversation's language, `user_confirmed` with confidence 1.0 and no source (`404`,
  `409` for the same content as a memory in use, `422` for text outside 1 to 280 characters); shared memories are not
  written yet. `PATCH` takes `content`, `subject`, `value` and `importance`, answers the memory, and records a revision
  (`409` for a duplicate, `422` for a bad value). `DELETE` answers `204`.
- **Lock and confirm.** `lock` and `unlock` write no revision and keep the version. `confirm` records a revision (actor
  `user`, reason `confirm`) and sets provenance `user_confirmed` and confidence 1.0; confirming a confirmed memory
  changes nothing, and `409` answers a memory that is not active or uncertain.
- **History.** `GET /memories/{id}/revisions` answers `{"items": []}`, newest first: `version`, `actor`, `reason`,
  `created_at` and the text, subject, value, status, provenance, confidence and importance the memory had under that
  version.
- **Episodes.** `GET /episodes` takes `character_id` (required), `conversation_id`, `q` and `status` (`active`, the
  default, or `invalidated`) and answers the newest 200 with `participants`, `sources` and `links` (`direction`,
  `relation`, the other episode's id and title).
- **Chat data.** `summaries` lists every checkpoint in any status with `level`, `from_seq`, `to_seq`, `version`, `status`,
  `text` and `content` (its lists and `source_seqs`). `relationship` answers `written` (false before a run wrote the
  state), `description` (the narrative, or the four bands in words), the narrative, expectations, boundaries, mood, topic,
  threads, goals, story changes, `recent_events`, `bands` and `values`, the four numbers, which a screen shows only as
  advanced. `relationship/history` answers the newest 200 change rows with `event_kind`, `dimension`, `reason`, `status`
  and `sources`. All three answer `404` for an unknown or deleted chat.
- **Events.** The WebSocket event `memory.updated` is not published by these routes yet.

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

Delivered in P03.3: `GET /jobs/{id}` (`lyrveil/api/jobs.py`). It answers `{"id", "kind", "status", "stage", "attempt",
"max_attempts", "queue_position", "error_code", "error_detail", "result", "created_at", "finished_at"}`, or `404
NOT_FOUND`. `queue_position` counts as for a send and is 0 once the job ended; `result` is `result_json`, null until the
job completes. The payload is never returned, because it can hold private text such as a draft's description. The
Creator polls this route while a draft runs, since `job.updated` is not published yet. Since P04.2a the `result` of a
`memory_extraction` job is `{"status", "run_id"}`, plus `run_attempt`, `from_seq` and `to_seq` for a `completed` or
`superseded` run; a completed one adds `attempts`, the counts `memories_added`, `memories_confirmed`, `episodes_added`,
`episodes_confirmed`, `sources_added`, `dropped_items`, `deferred_items`, and `memory_ids`. It never holds memory text
([Delivered in P04.2a](memory-engine.md#delivered-in-p042a)). Since P04.2b a completed one also has
`memories_superseded`, `memories_uncertain` and `conflict_ids`, and `deferred_items` counts only relationship events
([Delivered in P04.2b](memory-engine.md#delivered-in-p042b)). Since P04.6 a completed one also has `state_events`, the
relationship state rows the run wrote, and `deferred_items` counts only a proposed `current_topic`
([Delivered in P04.6](consistency-engine.md#delivered-in-p046)). The list, cancel and retry
routes are planned. Not in `backend/openapi.json` until it is next regenerated with Docker.

## Event WebSocket

Endpoint: `/ws`. One connection per browser tab. The server checks `Origin` and `Host`
before accepting, and refuses the handshake with `403 FORBIDDEN_ORIGIN` otherwise
([Security](security.md#local-only-model)).

Delivered in P01.7a: the checks, the handshake and the `connected` frame. Delivered in P02.5c: the `hello`, replay and
`resync.required`, and the reply events (`lyrveil/events/hub.py`, `lyrveil/api/events.py`). In-process tests cover it.
Since P02.6c the backend image serves it too: `websockets` 17.4 ([Technology](technology.md)) is in `pyproject.toml`
and `uv.lock`, so Uvicorn upgrades the connection. `tests/e2e/specs/reply-stream.spec.mjs` opens `/ws` through nginx
in the Compose test project and receives a reply's `typing.started`, `message.delta`, `typing.stopped` and
`message.completed` frames.

Rules of the delivered server, beyond the protocol below:

- The connection receives events only after its first `hello` is answered, so a live event never arrives before the
  older ones a replay brings. A later `hello`, `ping` and any other frame are ignored and change nothing.
- `last_event_id` null (a first connection): no replay; the events published since the connection opened follow.
- A replay needs the server's own `boot_id` and an `id` the server handed out whose followers are all still among the
  newest 2000. Otherwise, and for a `hello` whose fields have the wrong type, the server sends one `resync.required`
  frame, `{"v": 1, "id": N, "type": "resync.required", "ts": "...", "data": {}}`, where `N` is the newest event `id`:
  every event up to it was published after its change was committed, so a REST reload shows it, and the next `hello`
  resumes from it. `N` is 0 before the first event.
- A connection that falls more than 2000 events behind loses its queued events and gets one `resync.required` instead,
  so a slow client cannot make the backend's memory grow.
- Published so far, by the reply job only: `typing.started`, `message.delta`, `message.restarted`, `typing.stopped`,
  `message.completed`, `message.failed`, and `message.updated` when a failed run will be retried and the reply goes
  back to `queued` with empty text, or when a reply check ended it `failed` (its content is then the V4 notice, or
  empty). The REST routes, the job states, the GPU state and the rest publish nothing yet.
- `message.delta` `offset` counts UTF-16 code units of the text before the delta, the index unit of JavaScript strings.
  Since P04.8a the deltas are the text the [reply normalizer](consistency-engine.md#reply-normalization) releases, not
  the runtime's tokens: one delta can join several tokens, and a token the normalizer holds back gives no delta. The
  deltas joined are the reply's text, except when N4 cuts a reply that the length limit stopped; the content of
  `message.completed` is then shorter and replaces the streamed text.
- Since P04.8b `message.restarted` comes when a [reply check](consistency-engine.md#delivered-in-p048b) discards the
  text streamed so far: `check` is `V6`, `V10` or `V11` before the corrective generation, whose deltas start again at
  offset 0, or `V4` before the reply ends `failed` with a notice.

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

The client learns `boot_id` from the first frame the server sends on each connection, before any replay. It is not
an event and has no `id`:

```json
{"v": 1, "type": "connected", "boot_id": "0192f3a0-0000-7000-8000-000000000000"}
```

The client defined this frame in P01.5b, and the server sends it since P01.7a. `boot_id` is a UUID drawn once per
backend process, kept in `app.state.boot_id`. Until the client has seen a `boot_id`, it sends `null`.

The client in `frontend/src/app/core/realtime/events-socket.ts` follows these rules. It asks for a resync on its own
when it cannot name a position: a reconnection when no event was delivered yet, or a `boot_id` that differs from the
one it knew. It asks once per connection, whatever the number of causes. It delivers an event only if its `id` is above
the last one delivered, so a replay that overlaps is harmless. A `resync.required` frame is not delivered as an event:
it makes the client's `resync$` stream emit, and an `id` in that frame becomes the position of the next `hello`. The
client ignores frames of another `v`, of an unknown type, or that are not JSON. It sends `ping` every 25 seconds, and
reconnects after 1, 2, 5 and then every 10 seconds.

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
| `memory.conflict` | `memory_id`, `conversation_id` | Needs review. Since P04.2b, published after an extraction run that stored an uncertain memory of importance at least 3, with the new memory's id. |
| `notification.created` | `notification` | In-app notification. |
| `resync.required` | None | Reload through REST. |

Client frames: `hello` and `ping`. All actions go through REST.

`message.delta` carries private text and is never logged.

## Control API

Between backend and orchestrator only. Internal network, port 8100. Every request
except `GET /v1/health` needs `Authorization: Bearer <LYRVEIL_CONTROL_TOKEN>`. Requests carry
only enums, identifiers and numbers, never user text.

Implementation: partial, in `orchestrator/src/lyrveil_orchestrator/api.py`. The token
check, `GET /v1/health` and `GET /v1/preflight` came in P01.4c and are tested by T-ORC-10.
The check runs before routing, so a missing or wrong token gets the same `401` on every
path, whether or not a route exists there: `application/problem+json` with code
`UNAUTHORIZED` and `WWW-Authenticate: Bearer`. The scheme is case-insensitive, and the
token is compared with `hmac.compare_digest`. The application refuses to start with a
token that is not 64 hexadecimal characters. With the right token, an unknown path
answers `404`. No OpenAPI document is served: this table is the contract.

Every other route of the table came in P02.2, served by `ModelLifecycleManager`
(`lifecycle.py`), whose run loop the application's lifespan starts. The text path is
covered: start from mode `llm`, readiness, leases, drain and release verification. The
VRAM check before a load, so `blocked_reason` too, and the image flow come in P06.
`blocked_reason` is always `null` until then. Since P02.6c `lyrveil_orchestrator.main`, the
command of the `runtime` image, serves the application with Uvicorn on port 8100, over the
Docker socket and NVML. In `compose.test.yaml` the test-only image `lyrveil/orchestrator-fakes:test`
serves the same application over `FakeContainerGate` and `FakeGpuProbe` ([Testing](testing.md)).

Rules of the delivered routes:

- Refusals are `application/problem+json` with `code`: `409 NOT_READY` when the state is
  not the ready state of the lease's mode, or a mode change or a drain is pending;
  `409 LEASE_HELD`; `410 LEASE_EXPIRED`; `409 CONFLICT` for a mode change while draining
  and for `POST /v1/recover` outside `ERROR`. A body that breaks the schema gets FastAPI's `422`.
- `job_id` is 1 to 64 letters, digits, `-` or `_`. `ttl_s` is 1 to 120, default 30. A body
  with any other field is refused.
- The lease document is `{"id", "mode", "job_id", "ttl_s", "expires_in_s"}`.
  `expires_in_s` is the time left when the answer was sent, so the two containers'
  clocks never need to agree.
- `DELETE /v1/leases/{id}` answers `410 LEASE_EXPIRED` when the lease is no longer the
  current one, and changes nothing. `ok` returns to the ready state, `oom` and
  `runtime_error` go to `RECOVERING`.
- `POST /v1/drain` answers at once. The runtime stops when the current lease ends;
  poll `GET /v1/state` until `IDLE`. The state document adds `draining`.
- `GET /v1/events` answers `{"boot_id", "events"}`. Each event has `seq`, `at`, `from`,
  `to`, `reason`, `job_id`, `code` and `details`. `details` holds only VRAM values in MiB
  and exit codes. For another `boot_id`, every buffered event is returned.
- `ERROR` is left only through `POST /v1/recover`. The timed retry comes in P06.

`GET /v1/preflight` answers `200` with `ok`, the rows of [Preflight checks](operations.md#preflight-checks)
that the orchestrator judges, and the facts read through NVML. A failing row is in the
body, not in the status code. An example, with illustrative versions:

```json
{
  "ok": true,
  "checks": [
    {"check": "gpu_in_container", "label": "GPU in container", "status": "pass", "detail": "NVML works: NVIDIA GeForce RTX 4070"},
    {"check": "cuda_driver_version", "label": "CUDA driver version", "status": "pass", "detail": "CUDA 13.0, driver 596.36"},
    {"check": "total_vram", "label": "Total VRAM", "status": "pass", "detail": "12282 MiB"},
    {"check": "free_vram", "label": "Free VRAM", "status": "pass", "detail": "10793 MiB"}
  ],
  "gpu": {
    "device_name": "NVIDIA GeForce RTX 4070",
    "driver_version": "596.36",
    "cuda_driver_version": "13.0",
    "vram": {"total_mib": 12282, "used_mib": 1489, "free_mib": 10793}
  }
}
```

`gpu` is `null` when NVML fails. The Docker facts of this report are planned.

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
  "draining": false,
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
