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
