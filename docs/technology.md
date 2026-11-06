# Technology choices

Status: accepted (2026-11-01). Versions were read from the official registries on
that date. AI runtimes and models were read again on 2026-11-05. Lock files pin exact versions and hashes when each component is built.

## Version policy

- Runtimes and models are pinned to one exact release or revision with its hash.
- Application dependencies are pinned by `uv.lock` and `package-lock.json`.
- Base images use exact tags in `docker/` and `*/Dockerfile`, and digests from P09 on.
- Upgrading a runtime needs a re-verification of
  [Runtime contracts](runtime-contracts.md) and the GPU tests.

## AI runtimes and models

| Component | Pinned version | Source | Integrity |
| --- | --- | --- | --- |
| KoboldCpp | `v1.124.2`, commit `6d2fedf8624e5f05926af3191f9efdcb13c4133c`, asset `koboldcpp-linux-x64` (628,981,944 bytes) | `LostRuins/koboldcpp` | SHA-256 `c66ae0106673c7d9c41bafcc92c95bc5a6256709e9d853e5e2255bf536c1b069` |
| ComfyUI | `v0.43.0`, commit `426f5eea52a7ece1dd599fff4cb6cd1f352ddd24` | `Comfy-Org/ComfyUI` (the `comfyanonymous/ComfyUI` URL redirects there) | Git commit hash |
| PyTorch for ComfyUI | Build for CUDA 13.0, index `whl/cu130` | `download.pytorch.org` | Pinned with hashes in `docker/image/requirements.lock` at P05 |
| Text and image models | Five text profiles and three image profiles, each with a pinned revision, size and hash | Hugging Face, none gated | SHA-256 per file in [Model catalog](models.md) |

The default profiles total 21.38 GB on the target tier and 13.91 GB on the reduced tier, and need no Hugging Face
account. KoboldCpp `v1.124.1`, pinned on 2026-11-01, was withdrawn upstream
([ADR 0028](decisions/0028-koboldcpp-pin-v1-124-2.md)).

KoboldCpp publishes Linux binaries with CUDA included, so its image starts from the
pinned binary. ComfyUI's README documents no Docker image and its release has no Linux
asset, so its image starts from the pinned source. Lyrveil builds both images itself
([ADR 0007](decisions/0007-own-runtime-images-pinned.md), pin updated by
[ADR 0028](decisions/0028-koboldcpp-pin-v1-124-2.md)).

## Backend and orchestrator

| Component | Version | License | Why |
| --- | --- | --- | --- |
| Python | 3.12, image `python:3.12.16-slim-trixie` | PSF-2.0 | Required. Debian trixie ships SQLite with FTS5. |
| FastAPI | 0.162.0 | MIT | Required. Typed routes, OpenAPI, WebSocket. |
| Uvicorn | 0.54.0 | BSD-3-Clause | ASGI server, started with one worker by `python -m lyrveil.main` ([Backend](backend.md#concurrency-model)). |
| Pydantic | 2.14.2 | MIT | Required. Validates API data and every structured model output. |
| pydantic-settings | 2.16.2 | MIT | Typed `.env` configuration. |
| SQLAlchemy | 2.1.6 | MIT | Required. Synchronous ORM ([ADR 0009](decisions/0009-synchronous-sqlalchemy.md)). |
| Alembic | 1.20.1 | MIT | Required. Migrations, including raw SQL for FTS5. |
| HTTPX | 0.28.1 | BSD-3-Clause | Async HTTP to runtimes and to the Docker socket. |
| websockets | 17.4 | BSD-3-Clause | WebSocket server support and ComfyUI progress client. |
| python-multipart | 0.0.33 | Apache-2.0 | Avatar and reference uploads. |
| Jinja2 | 3.1.6 | BSD-3-Clause | Prompt templates with `StrictUndefined`. |
| Pillow | 13.0.0 | MIT-CMU | Validates uploads, crops, writes thumbnails. |
| snowballstemmer | 3.1.1 | BSD-3-Clause | Italian and other stemming for FTS5, no model needed. |
| structlog | 26.1.0 | MIT or Apache-2.0 | Structured JSON logs. |
| nvidia-ml-py | 13.615.71 | BSD-3-Clause | NVML access. Orchestrator only. |
| tzdata | 2026.6 | Apache-2.0 | Time zone data with no network access. |

Not used: `docker` SDK, because a small HTTPX client limits the orchestrator to its
allowlist and removes a dependency from the most privileged service. `aiosqlite`,
because of [ADR 0009](decisions/0009-synchronous-sqlalchemy.md). AnyIO comes with
Starlette and provides `to_thread`.

## Frontend

| Component | Version | License | Why |
| --- | --- | --- | --- |
| Node.js | 24, image `node:24.22.2-alpine` (build only) | MIT | Inside Angular's supported range `^24.15.0`. |
| Angular framework | 22.2.4 | MIT | Active until 2027-06. Zoneless by default since v21. Signal Forms stable since v22. |
| Angular CLI and `@angular/build` | 22.2.5 | MIT | Build and Vitest integration. |
| `@angular/aria` | 22.2.5 | MIT | Headless accessible listbox, menu, tabs, combobox. |
| `@angular/cdk` | 22.2.5 | MIT | Overlay, focus trap, virtual scrolling. |
| TypeScript | 6.0.3 | Apache-2.0 | Angular 22 requires `>=6.0 <6.1`. The npm `latest` tag is 7.0.2 and must not be used. |
| RxJS | 7.8.2 | Apache-2.0 | Required by Angular. Used only at HTTP and WebSocket boundaries. |
| tslib | 2.9.1 | 0BSD | TypeScript helpers, required by Angular with `importHelpers`. |
| `@hey-api/openapi-ts` | 0.99.0 | MIT | Generates API types from the backend OpenAPI document. `openapi-typescript` 7.13.0 is not used because its peer range stops at TypeScript 5. |
| `@fontsource-variable/inter` | 5.3.0 | OFL-1.1 | Message and interface text, bundled for offline use. |
| `@fontsource/pixelify-sans` | 5.3.0 | OFL-1.1 | Brand, short titles and small labels. |
| `@fontsource/jetbrains-mono` | 5.3.0 | OFL-1.1 | Identifiers and diagnostics. |
| nginx | image `nginxinc/nginx-unprivileged:1.30.6-alpine` | BSD-2-Clause | Static files and reverse proxy, runs as non-root. |

Rendering is client-side only. SSR adds a server without any benefit for a local
single-user app.

## Infrastructure and tools

| Tool | Version | License | Use |
| --- | --- | --- | --- |
| Docker Engine | 29.9.2 measured, API 1.45 or later required, for volume subpaths | Apache-2.0 | Containers. |
| Docker Compose | v5.6.1 measured, v2.30 or later required | Apache-2.0 | `gpus: all` and profiles. |
| uv | 0.13.6 | MIT or Apache-2.0 | Python lock files and installs. |
| Ruff | 0.17.3 | MIT | Lint and format. |
| mypy | 2.4.1 | MIT | `--strict`, with the Pydantic plugin. |
| pytest, pytest-asyncio, pytest-cov | 9.2.0, 1.4.1.post1, 7.1.0 | MIT, Apache-2.0, MIT | Tests. |
| Hypothesis | 6.170.14 | MPL-2.0 | Property tests of the state machine. Development only. |
| respx | 0.23.1 | BSD-3-Clause | HTTP stubs in unit tests. |
| Schemathesis | 4.33.0 | MIT | Contract tests from OpenAPI. |
| Vitest | 5.0.5 | MIT | Angular unit tests. |
| jsdom | 31.0.0 | MIT | DOM for Vitest through `ng test`. Development only. |
| ESLint and `@eslint/js` | 10.14.0 and 10.0.1 | MIT | Frontend lint, with the T-SEC-09 restrictions. Development only. |
| typescript-eslint | 8.72.1 | MIT | TypeScript rules. Its range `<6.1.0` accepts TypeScript 6.0.3. Development only. |
| angular-eslint | 22.6.0 | MIT | Angular and template rules, and the template parser. Development only. |
| Playwright | 1.64.1 | Apache-2.0 | End-to-end tests. |
| `@axe-core/playwright` | 4.14.0 | MPL-2.0 | Accessibility checks. Development only. |
| shellcheck | Debian package | GPL-3.0 | Linting of shell scripts inside images. Development only, not distributed. |
| PSScriptAnalyzer | Pinned at P01 | MIT | Linting of the Windows scripts. Development only. |
| Pester | Pinned at P01 | Apache-2.0 | Tests of the Windows scripts. Development only. |
| gitleaks | v8.30.1 | MIT | Secret scan. |
| pip-audit | 2.10.1 | Apache-2.0 | Python advisories. |
| pip-licenses | 5.5.5 | MIT | Python license report. |
| license-checker-rseidelsohn | 5.0.1 | BSD-3-Clause | npm license report. |

The orchestrator's async tests run with the pytest plugin of AnyIO, which HTTPX already brings, so its test stage
does not install pytest-asyncio.

## Alternatives considered

| Topic | Alternative | Why not |
| --- | --- | --- |
| Queue | Redis, Celery, RQ | A new service for one process. SQLite gives durable jobs ([ADR 0010](decisions/0010-job-queue-and-scheduling-policy.md)). |
| Database | PostgreSQL | Excluded by the requirements. One writer on one machine does not need it. |
| Memory search | Embeddings, vector database | Excluded by the requirements. FTS5 with stemming comes first ([ADR 0011](decisions/0011-memory-retrieval-fts5-without-embeddings.md)). |
| Model switching | KoboldCpp `--admin`, ComfyUI `POST /free` | Weaker guarantee than stopping the process ([ADR 0005](decisions/0005-exclusive-gpu-by-container-lifecycle.md)). |
| Text endpoint | `/v1/chat/completions` | Each model family needs its own exact template, and one GGUF has none. `/api/v1/generate` gives full control over the prompt ([ADR 0025](decisions/0025-prompt-templates-per-model-family.md)). |
| Forms | Reactive Forms | Signal Forms are the current recommendation for new Angular 22 forms. |
| UI library | Angular Material, PrimeNG | Their visual language fights Obsidian Bloom. `@angular/aria` gives behavior without style. |
| i18n | `@angular/localize`, Transloco | Compile-time locales or a dependency for a single-language 1.0. A small signal-based service suffices ([Frontend](frontend.md#text-and-translation)). |
