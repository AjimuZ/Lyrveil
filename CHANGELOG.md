# Changelog

All notable user-visible changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

No application exists yet. This section records the design documentation.

### Added

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

### Changed

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
