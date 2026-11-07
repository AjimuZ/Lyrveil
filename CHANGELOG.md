# Changelog

All notable user-visible changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

No application exists yet. This section records the design documentation.

### Added

- Editing, deleting or regenerating a message now updates what the character remembers from it. In the same save, the
  memories, events, relationship and mood changes and summaries that rested on the old text stop being used: a memory
  that another message also supports stays, with a little less confidence, and a memory you locked is never changed,
  only marked for your review. The messages around the change are then read again in the background, so what still
  holds comes back on its original memory, and a relationship change is never counted twice. Deleting the message
  where you said something changed (for example a new job) brings the earlier fact back. The summaries covering the
  message are written again. Re-reading old messages never lets an old message undo a newer fact. Not yet: the Memory
  Studio view of memories marked for review.
- Relationship state per chat. Each memory extraction now also records how the relationship moved (familiarity, trust,
  confidence and tension), the character's current mood, the topics left open and closed, goals and changes in the
  story, each with the reason and the messages it came from. Changes are small and bounded: at most 8 points per
  dimension per extraction, 15 per day (in the configured time zone), always between 0 and 100, and a big jump needs an
  important event in the same messages. The values stay internal; once a chat has a state, each reply's prompt describes
  it in words ("vi state conoscendo, sei prudente con…", the mood, the open topics), never as numbers. A state change
  in one chat never affects another chat. The extraction prompt now lists the open topics so the model can close
  them. Not yet: the current topic, a scenario's starting relationship, the user's corrections and the Memory Studio
  view. Since the invalidation step, the state is recomputed when a message is edited or deleted.
- Replies now use the chat's memory and the whole character card. Each reply's prompt holds the global rules, the full
  identity with its traits, interests and background, the communication style with the example exchanges (in saved
  order, as many as fit about 500 tokens), the chat's summaries of older messages, the memories most relevant to the
  last messages, and a short reminder just before the user's message with the name, age, the first sentence of two
  main traits, the address form, the length of the character's replies and the asterisk and emoji rules. The
  character's message length now sets how long a reply may be: about 60, 120, 240 or 400 tokens for "brevissimi",
  "brevi", "medi" and "lunghi", never above the 400-token ceiling. Everything is sized with the loaded model's own
  token counts. When the model's window is small, the extras are cut in a fixed order (examples, older summaries,
  less relevant memories, older messages down to a floor, optional card details) and the essentials of the card, the
  reminder and the user's message always stay. A message longer than about 2048 tokens is cut in the prompt only, with
  no note in the interface yet. The memories a reply used are marked as used. Time of day is not in the prompt yet
  (the relationship state is, see above), and the per-reply record for the Memory Studio is not stored yet.
- Progressive summaries, backend only. Once a chat has 40 older messages (or enough to reach about 2400 tokens) that
  are not among the newest 30, a background job summarizes them with the loaded text model and stores the summary as a
  checkpoint over that range of messages, with the events, decisions, relationship changes, open points and key facts.
  When six consecutive checkpoints exist, the oldest four are merged into one longer-range checkpoint; the originals and
  the merged checkpoints are kept. A range summarized again gets a new version and the old one is marked superseded,
  never overwritten. The job runs only while the text model is loaded and retries after 1 and 5 minutes when the model's
  answer is invalid. Replies use them (see above); nothing shows them, and checkpoints are not yet invalidated when a
  message is edited or deleted.
- Memory extraction, backend only. Once a chat has 10 messages not yet analyzed, or 2 when the user wrote a phrase
  such as "ricordati", "ho cambiato" or "ti prometto", the reply that follows queues a background job. The job reads
  up to 12 of those messages with the loaded text model and stores the facts and events worth remembering, each with
  the messages it came from. It runs only while the text model is loaded, retries after 1 and 5 minutes when the
  model's answer is invalid, and never analyzes the same messages twice. Replies use these memories (see above);
  nothing shows them. Relationship changes and the chat state are stored too (see above).
- Memory consistency rules, backend only. Code, not the model, now decides what extraction stores. A fact that
  changed ("ho cambiato lavoro, ora sono da Verdi") replaces the old one, which is kept as history with its source
  messages. Two facts that disagree with no clear change are both kept as uncertain, and a conflict that matters raises
  a `memory.conflict` event. An explicit correction by the user wins and is stored as confirmed. Something true only
  for now ("questa settimana sono a Roma") expires after its date, or after 7 days, without replacing the lasting
  fact. A guess never overrides what the user said, and a fact about the user that only the character said is stored
  as a guess. A repeated or near-identical fact adds its sources to the one already stored. Locked memories never
  change, and proposals to change the character's profile are kept aside and never applied. Every change to a memory
  keeps the earlier version. Resolving conflicts and the Memory Studio are not delivered yet.
- Memory edits and deletions, backend only. Correcting a memory keeps its earlier text as a revision. Deleting a memory
  removes its text, history and sources and leaves only hashes, so analyzing the same messages again cannot bring it
  back, while a later message that states the fact again can. Nothing in the app calls this yet.
- Memory retrieval, backend only. For each reply it can pick the few memories and events of this chat that matter
  most, by the words of the last messages, importance, how recent they are, the current topic and the kind of memory,
  with rules, promises and locked memories always considered. It fits them into a token budget, keeps both sides of a
  conflict together and marks each one as sure, uncertain, story, for now, past or event. A memory of one chat never
  reaches another chat or another character; memories shared across chats are read only when the chat has sharing on.
  On a synthetic set of 60 questions it finds 58 of the 61 expected memories within the first 8. Each reply now uses
  it.
- Daily backups. The backend writes one backup of all application data a day, at its first idle moment (no running
  job and no write for a minute), in the same format as `scripts\lyrveil.cmd backup`, and keeps the newest 7 in the
  data volume; the backup command prunes the same way. `start.cmd` and `stop.cmd` now copy every backup missing from
  the Windows backup folder (`LYRVEIL_BACKUP_DIR`), which is never pruned; a failed copy prints a warning and never
  fails the start or the stop. A restore of a copy from that folder brings back characters, conversations, assets,
  settings and launch files. **Impostazioni > Dati** and the backup before migrations are not delivered yet.
- Scenarios. The **Scenari** screen creates, edits, searches, archives and deletes scenarios (title, description,
  setting, opening message, an optional character and a language), and **Inizia conversazione** on a scenario opens the
  new-chat picker with it chosen; the picker also has an optional **Scenario** choice. A chat started from a scenario
  keeps its own copy: its opening message becomes the first message of the character, its description and setting reach
  the reply prompt of that chat only, and editing, archiving or deleting the scenario afterwards changes nothing in it.
  A scenario text that describes a minor is refused with `CONTENT_POLICY_BLOCKED`. A character can have any number of
  conversations, each with its own history and context. The routes are `GET`, `POST /api/v1/scenarios`, `GET`, `PUT`,
  `DELETE /api/v1/scenarios/{id}` and `POST /api/v1/scenarios/{id}/archive` and `.../unarchive`; they are not in
  `backend/openapi.json` yet.
- Three original demo characters (Nerea Falchi, Ilario Sant'Elmo and Tessa Moravi), adults with Italian voices in
  three registers, can be added on request with `POST /api/v1/characters/demo`. Nothing is loaded by default, loading
  twice adds nothing, and an existing character is never changed. The first-start question and the screen button are
  not delivered yet.
- Characters must be adults in their text too: creating, saving, importing, duplicating or restoring a character whose
  text names a minor (such as "bambina", "adolescente", "teenager"), states an age under 18 ("ha 16 anni",
  "sedicenne", "16 years old") or puts childhood next to sexual content is refused with `CONTENT_POLICY_BLOCKED`, and
  the message names the field. The character's own past in a fixed form, such as "Da bambina sognava il mare", is
  accepted. **Bozza assistita** refuses such a description before the model runs, and a draft whose text describes a
  minor is corrected once, then fails. The checks are fixed Italian and English word lists that no setting can turn
  off. They are best-effort: they can refuse an innocent sentence, such as a teacher writing about "i bambini", or miss
  a phrase.
- Character export and import, backend only: `GET /api/v1/characters/{id}/export` downloads a character as a
  `.lyrveil-character.json` file with its card and the type and SHA-256 of each image (`include_assets=true` also embeds
  the images), and `POST /api/v1/characters/import` creates a new character from such a file, never overwriting one.
  The file is checked in full before anything is written: a newer format, an unknown field, an age under 18, a wrong
  image hash or an image that is not a real PNG, JPEG or WebP are refused with `IMPORT_INVALID`; files over 64 MB with
  `413`. `GET /api/v1/characters/format-schema` publishes the JSON Schema of the format. There is no button for it in
  the Creator yet.
- Avatar tools in the Character Creator (Images tab, for a saved character) and in the profile: choose an image and
  see a preview before it is sent, crop a variant to a square (saved as a new variant, the original stays), choose the
  primary avatar among the variants. A refused upload shows the Italian message of its error code. The conversation list
  now shows the primary avatar instead of the initial (`avatar_url` on conversations). Deleting a variant from the
  screen is not offered yet.
- Character avatars and pictures, backend only: `POST /api/v1/characters/{id}/assets` uploads a PNG, JPEG or WebP image
  (`avatar`, `profile` or `reference`) up to 15 MB and 8192 x 8192 pixels. Every upload is decoded and re-encoded, so
  location, camera and editor metadata never reach the stored file, and gets a WebP thumbnail. The first avatar becomes
  the primary one; `POST .../{asset_id}/primary` switches it, `POST .../{asset_id}/crop` saves a 512 x 512 crop as a new
  picture and keeps the original, `GET .../assets` lists them and `DELETE` removes one, except the primary avatar. Files
  are served at `/media/assets/{id}` and `/media/assets/{id}/thumbnail`. Oversized images and decompression bombs are
  refused with `413`, files that are not real images or whose type is disguised with `415`. Purging a character now
  removes its pictures too. The routes are not in `backend/openapi.json`; the upload and crop screens, generated
  avatars and the avatar of a duplicate are not delivered yet.
- **Bozza assistita** in the Character Creator: describe a character in Italian and the text model drafts every field of
  the card, which the form then shows for review. The draft is never saved by itself: the form stays unsaved, a note
  lists the fields the model proposed, and only **Salva personaggio** creates or changes the character. On the edit
  screen the draft rewrites the card but keeps the locked traits and appearance fields. The model writes under a
  grammar generated from the card schema, its answer is validated, and an invalid answer is corrected once; a second
  failure shows the reason, such as "L'età deve essere almeno 18.". While it waits, the panel shows the queue position.
  The routes `POST /api/v1/characters/drafts` and `GET /api/v1/jobs/{id}` are new and not in `backend/openapi.json`. The
  content check on the description is not delivered yet (P03.6).
- The character profile at `/characters/:id`: identity summary, personality, example exchanges, the character's active
  conversations, a note that the character is simulated, and the version history with **Confronta** (field-by-field
  comparison with the current version) and **Ripristina** (adds a new version, nothing is rewritten). **Duplica**
  copies the card into a new character called "<name> (copia)" without conversations or memories. **Archivia** hides the
  character and its conversations from the lists and **Rimuovi dall'archivio** brings them back. **Elimina
  definitivamente** shows how many conversations, messages and versions will go and needs the first name typed before it
  deletes them all for good. The routes `POST /api/v1/characters/{id}/duplicate`, `/archive`, `/unarchive`, `GET
  .../purge-preview` and `POST .../purge` are new and not in `backend/openapi.json`. The avatar, the memory studio and
  the proposals are not delivered yet, and the end-to-end test for the profile was written but not run.
- The Character Creator at `/characters/new` and `/characters/:id/edit`, in four tabs: **Identità**, **Personalità**,
  **Comunicazione** and **Aspetto**. It edits the whole card: how the character speaks of itself, traits with locks,
  *tu* or *Lei*, narration and emoji with a one-line example, example exchanges, phrases to avoid, the Italian
  appearance descriptions with locks, the English identity slots chosen from the tag vocabulary (searchable by Italian
  words), the usual outfit and up to 5 named outfits. Fields refuse an age under 18, a reserved or repeated outfit key
  and a tag outside the vocabulary with Italian messages before anything is sent. A new character keeps a draft in
  the browser, and leaving with unsaved changes asks first. The new-chat dialog links to it. `GET
  /api/v1/characters/vocabulary` supplies the tags. The assist panel, import and export, avatar actions, the
  characters list and the profile are not delivered yet. The end-to-end test for the flow was written but not run.
- Saving a character card and its history in the backend: `PUT /api/v1/characters/{id}` saves the whole card and
  records a version only when something changed, `GET .../versions` lists the versions, `GET .../versions/{n}` returns
  one, `GET .../versions/{a}/diff/{b}` compares two field by field, and `POST .../versions/{n}/restore` adds a new
  version holding the old content without rewriting history. Locked traits and locked visual fields are kept when an
  automatic proposal is applied; the user's own save can still change them. Migration `0007` adds `character_versions`
  and gives every existing character its version 1. The Creator screens, duplicate, archive, purge and export are not
  delivered yet, and the new routes are not in `backend/openapi.json`.
- The character card in the backend: `POST /api/v1/characters` now takes the whole card (identity, personality traits,
  communication with *tu* or *Lei*, narration, emoji, example exchanges and phrases to avoid, and the visual profile
  with its English identity slots, usual outfit and up to 5 named outfits), and `GET /api/v1/characters/{id}` returns
  it. Ages under 18 are refused by the API and by a database CHECK; slot tags must come from the new tag vocabulary
  (`backend/src/lyrveil/images/vocabulary/tags.v1.json`) and free tags are sanitized. Migration `0006` adds
  `character_traits` and `character_visual_profiles`. Saving changes, versions and the Creator screens are not
  delivered yet, and the new routes are not in `backend/openapi.json`.
- The orchestrator service now runs: `python -m lyrveil_orchestrator.main` serves the control API on port 8100 over
  the Docker socket and NVML, so its Compose health check can pass. The backend image gains the `websockets` library,
  so `/ws` accepts connections and a reply streams to the browser. In the Compose test project a sent message gets a
  lease and a streamed, stored reply from the fake KoboldCpp. `start.cmd` does not start the orchestrator yet. Not yet
  tried with a real GPU and Docker socket.
- The conversation screen at `/chats/:conversationId`: messages under day separators such as `sabato 1 novembre`, a
  composer (Enter sends, Shift+Enter adds a line, a counter near the 8000-character limit), a typing indicator and
  the reply text as it streams in, **Modifica** (with the warning about linked memories on an older message),
  **Elimina** with a confirmation, **Copia**, **Rigenera** and `‹ 2/3 ›` alternatives on the latest reply, one check
  on sent messages, older history on **Mostra messaggi precedenti**, and recovery of a reply after a page reload. A
  message that fails to send stays on screen with **Riprova invio**. Message text always renders as text, never as
  markup. The context panel, images, search in a chat, "Ricorda questo" and virtual scrolling are not part of it yet.
  Opening or reloading a chat by its address now works: the page used to stay empty because a `<base>` element was
  refused by the security policy.
- The chat list screen at `/chats`: search over names, titles and messages, an **Attive** and an **Archiviate** tab,
  and a menu on each chat to pin, archive or unarchive, and delete, with a **Ripristina** notice after a delete. **Nuova
  conversazione** opens a dialog that picks a character and a language, or creates a character with a minimal form
  (name, optional last name, age of at least 18, how it speaks of itself, language) until the Creator arrives in P03.
  A chat opens a placeholder until the conversation screen is delivered (P02.6b). Previews, unread counts, avatars and
  scenarios are not shown, because nothing behind them exists yet. The backend gains `GET` and `POST /api/v1/characters`
  for the form.
- Search in the backend: `GET /api/v1/conversations/{id}/search?q=` over message text and `q` on the chat list over
  titles and messages, with FTS5 (migration `0005`). Accents are ignored ("perche" finds "perché"); search text is
  plain words, so operators, quotes and parentheses never raise or change a query. No stemming yet.
- Conversations in the backend: create a chat for a character, list (pinned first, then by activity), pin, archive,
  soft-delete and restore within 30 days, through `/api/v1/conversations`. Migration `0003` adds the `conversations`
  table and the minimal `characters` table it refers to. The routes are not in `backend/openapi.json` yet.
- Messages in the backend: send with a `client_message_id` that makes retries return the first result, saved before
  its `chat_reply` job is queued; edit, soft-delete, regenerate the last reply into variants and switch between them,
  and page through history, through `/api/v1/conversations/{id}/messages` and `/api/v1/messages/{id}`. Migration `0004`
  adds `messages` and `message_variants`. The routes are not in `backend/openapi.json` yet.
- Streaming replies in the backend: the job worker now runs with the application and answers each queued reply or
  regeneration through KoboldCpp, one at a time in queue order, with a prompt made of the character's name, age and
  basic card fields and the recent chat. The text is saved every 2 seconds and at the end; a runtime failure is
  retried once from a clean reply, and a reply cut by a crash or a shutdown is shown as interrupted with what it had
  written. The event WebSocket `/ws` now sends the reply's typing, token, completion and failure events, replays what a
  reconnecting tab missed from the newest 2000 events, or asks it to reload. Not yet usable end to end: the chat
  screens come with P02.6.
  Replies are not yet normalized or checked (P04.8), and sampling uses the `gemma4` defaults whatever the profile.
- The model catalog, `config/model-catalog.json`, with the five text and three image
  profiles, each pinned to a revision with its size and SHA-256 as `docs/models.md`
  lists them, and the backend validator that refuses a bad catalog. The tier defaults
  are Gemma 4 26B-A4B on the 32 GB tier, Gemma 4 12B on the 16 GB tier and Illustrious
  XL v2.0 for images. The catalog is in the backend image, so `scripts\lyrveil.cmd
  models` can now read it; no model has been downloaded or loaded yet, and nothing
  starts the validator at startup yet.
- The text runtime image, `lyrveil/llm:local`, built from the pinned KoboldCpp
  `v1.124.2` binary, whose size and SHA-256 the build checks. Setup now builds it,
  which downloads 629 MB from GitHub once, and creates its container; setup has not
  yet run this way on a real installation. The image has not yet loaded a model on
  a GPU.
- A GitHub Actions workflow that, on every push, runs lint, type checks, unit and
  integration tests, the end-to-end tests with fakes, the image builds of the
  frontend, backend and orchestrator, a gitleaks scan of the pushed commits, and a
  parse of every Mermaid diagram. It has not run yet, and it never runs the GPU tests.
- Schemathesis contract tests of the backend's OpenAPI document, in the backend's
  integration tests. They ran and passed locally; the CI job has not run them.
- Product requirements with stable identifiers and acceptance criteria.
- Architecture for the five Docker services, the data model, the REST, WebSocket
  and control APIs, and the verified contracts of the two runtimes.
- Design of the Lyrveil Memory Engine, the Character Consistency Engine, the Model
  Lifecycle Manager, image generation, proactive messages and the Character Creator.
- The Obsidian Bloom design system and the specification of the ten screens.
- Operations, security, licensing, testing and publication guides, a phased
  roadmap with exit checks, a risk register and 36 architecture decision records.
- Repository hygiene files that keep model weights, user data and secrets out of
  Git and Docker images.
- A model catalog: the user chooses the active text and image models from verified
  profiles, and can register local files as unverified custom profiles.
- Two image quality presets: **Alta**, a two-pass render up to 1248 x 1824, and
  **Rapida**, a single pass.
- A short reminder of the character before each reply, with the changing context
  placed after the history, so long chats keep the identity and reuse the cache.
- The same reply rules for every text model: no speaker labels or Markdown, the
  character's narration and emoji style, length bands, and one corrected
  regeneration when a reply gets the name, age or grammatical gender wrong.
- Card fields for grammatical gender, `tu` or `lei`, narration, emoji, example
  lines and phrases to avoid, and a setting for how characters address the user.
- A consistency protocol and an extraction evaluation that every catalog text
  profile must pass, shown as **Qualità verificata**.
- Structured visual identity, a wardrobe of outfits, age bands, an original tag
  vocabulary and a sanitizer for image prompts.
- Structured image scenes, automatic portrait or landscape sizes, image captions in
  the character's voice, and measured image quality levers.
- `scripts\lyrveil.cmd test lint` checks the documentation's links, anchors,
  traceability and test catalog, and analyzes every PowerShell script with a pinned
  PSScriptAnalyzer. `test unit` runs the Pester tests of the scripts. Levels that
  have no tests yet exit with an error instead of reporting a pass.
- A backend skeleton built in Docker: typed deployment settings that stop startup
  with a message naming any invalid variable, JSON logs that keep only allowlisted
  fields so no message text or prompt is logged, and a locked set of dependencies.
  `test lint` also runs ruff and `mypy --strict` on the backend, and `test unit`
  runs its pytest unit tests; arguments after `test unit` go to pytest, so one
  test can run alone.
- Backend persistence: a SQLite database in the data volume that the backend
  checks at startup. It refuses to start unless WAL mode and FTS5 really work,
  applies the Alembic migrations, and exposes `GET /api/v1/health/live` and
  `GET /api/v1/health/ready`. The ready report lists the database, the
  migrations and FTS5, and shows the control API on its own line.
  `scripts\lyrveil.cmd test integration` runs the backend integration tests.
- An orchestrator package with the GPU state machine as a table: only the moves
  of the documented diagram are accepted, and every other one is refused. `test
  lint` and `test unit` also run the orchestrator's gates and tests. A test named
  after `test unit` runs only in the component that holds it, and `test
  integration` skips a component that has no integration tests.
- The orchestrator's container gate, the only code that talks to Docker. It can
  inspect, start, stop and read the logs and processes of the two runtime
  containers and nothing else. It refuses any other container, including one
  with the same name that the Lyrveil Compose project did not create, and it has
  no way to create, run commands in or remove containers, or to touch images,
  volumes or networks.
- The orchestrator's GPU preflight, `python -m lyrveil_orchestrator.preflight`:
  it reads the card through NVML and reports NVML, the CUDA driver version, total
  VRAM and free VRAM as pass, warning or failure at the documented thresholds,
  with `--json` for scripts. It exits 1 only when a row fails.
- The orchestrator's control API with its token: every request except
  `GET /v1/health` needs `Authorization: Bearer` with `LYRVEIL_CONTROL_TOKEN`,
  compared in constant time, and anything else gets `401`. `GET /v1/preflight`
  returns the GPU preflight. `test integration` now runs the orchestrator's
  integration tests too.
- A first web interface in `frontend/`: an Italian shell with the Obsidian Bloom
  colors, bundled fonts that need no network, a navigation bar and every
  documented screen address, each saying that the screen is not available yet.
  `test lint` also runs ESLint, which forbids rendering HTML from strings or
  bypassing Angular's sanitizer, a check that styles use only the design tokens,
  and a production build that fails on any warning or network reference. `test
  unit` also runs the frontend's Vitest tests. The image's `Dockerfile` and
  `nginx.conf` are written but not built yet.
- The web interface can now talk to the backend: TypeScript types generated from the
  backend's OpenAPI document, which is committed and checked by a backend test,
  failed requests turned into errors with Italian messages, and a client of the
  event WebSocket that reconnects with backoff, replays from the last event and
  reloads from the REST API when the server reports a gap. No screen uses them yet.
  `test lint` fails when the generated types are out of date.
- `setup.cmd` prepares an installation. It checks Git, Docker, Compose, the Engine
  API, the memory Docker sees and the free disk, and exits 2 with advice for each
  failure, such as the `.wslconfig` values for the machine's RAM. It then creates
  `.env` with a new control token, readable only by the current Windows user and
  never overwritten. It creates the volumes `lyrveil-data` and `lyrveil-models`
  with their label and owner, builds the images and creates the runtime containers
  without starting them. A second run repeats nothing. Model downloads are not part
  of it yet, and `-AcceptModelLicenses` and `-VerifyModels` exit 1 until they are.
  `scripts\lyrveil.cmd test docker` runs setup twice against a separate test
  project.
- `start.cmd` runs setup when something is missing, checks Docker and the two
  volumes, runs the GPU checks, starts the backend and the frontend, waits up to
  120 s for their health checks and ends with `Lyrveil è pronto:
  http://localhost:4200`. It exits 2 when a check fails and 3 with the last logs
  when a service does not become healthy. The model catalog, the text model, the
  orchestrator and the backup copy are not available yet: `start.cmd` warns about
  each one and does not fail.
- `stop.cmd` stops the services and keeps containers, volumes and data, also when
  they are already stopped; it never runs `docker compose down`. `restart.cmd`
  runs both. `status.cmd` shows each service, its health, the URL and the free
  disk, and `status.cmd -Json` gives the same report as JSON. `logs.cmd` shows the
  logs, with `-Follow`, `-Since` and `-Tail`.
- `scripts\lyrveil.cmd models` downloads the model files of the catalog in the
  one-shot `lyrveil-models` container: the default profiles of the machine's
  tier, `-Model <id>` or `-All`. It shows each license and asks first, or takes
  `-AcceptModelLicenses`; with no answer it downloads nothing and exits 5.
  Downloads use HTTPS only, resume with range requests, and keep nothing whose
  size or SHA-256 differs from the catalog (exit 4); verified files are skipped.
  `-List`, `-Verify` and `-Remove <id>` list, re-hash and delete files, and
  `-Remove` refuses an active profile and asks first. The catalog arrives with a
  later release, so until then the command only reports that it is missing.
- `scripts\lyrveil.cmd import-model <file>` copies a `.gguf` into the text model
  folder or a `.safetensors` into the image checkpoints of the models volume, in
  a container with no network that reads the file's folder read-only. It refuses
  other extensions, names outside `^[A-Za-z0-9._-]{1,128}\.(gguf|safetensors)$`
  and names already present, and never replaces a file.
- `scripts\lyrveil.cmd backup` writes `lyrveil-<UTC time>.tar` with the database,
  media, launch files and a manifest of SHA-256 hashes into the data volume, then
  copies every backup missing from the Windows backup folder
  (`LYRVEIL_BACKUP_DIR`, by default `Documents\Lyrveil\backups`).
- `scripts\lyrveil.cmd restore <file>` checks the backup first and refuses a
  damaged one or one from a newer version without stopping anything. After a
  confirmation it stops Lyrveil, keeps the current data in
  `pre-restore-<time>`, puts the backup's data in place and starts again.
- The backend answers only the local app. A request whose `Host` is not
  `localhost` or `127.0.0.1` on `LYRVEIL_HTTP_PORT`, which blocks DNS rebinding,
  and a write or WebSocket handshake from another `Origin`, get `403` with the
  code `FORBIDDEN_ORIGIN`. The backend and `nginx.conf` both send the documented
  security headers on every answer, each header once, errors included, and an
  unexpected failure answers a generic `500 INTERNAL`. The event WebSocket `/ws`
  now accepts connections and sends its `connected` frame; events come later, and
  the image cannot open it yet because the WebSocket library is not installed.

### Changed

- The web interface's container now runs as UID and GID 10001, like every other
  service, instead of the nginx image's own user. Docker tests check that
  only `127.0.0.1:4200` is published, that the backend and the runtimes have no
  route to the Internet, and that the images hold no data, weights, `.env` or
  test fakes.

- The default text model is Gemma 4 26B-A4B on 32 GB machines and Gemma 4 12B on
  16 GB machines, instead of Rocinante 12B v1.1, which stays selectable.
- The default image model is Illustrious XL v2.0 instead of v1.0, which stays
  selectable. NoobAI-XL 1.1 is also offered.
- Prompts follow the official template of each model family.
- Image prompts are built from a structured scene and a fixed identity, instead of
  a free English prompt written by the text model.
- The 32 GB machine is the target tier, and KoboldCpp is pinned to `v1.124.2`
  because `v1.124.1` was withdrawn upstream.
- Lyrveil is installed and run from Windows: `.cmd` entry points replace the Bash
  scripts, live data and models live in Docker volumes, and a daily backup is
  copied to a Windows folder. No Linux distribution is needed.
