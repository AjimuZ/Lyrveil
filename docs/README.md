# Documentation

**Status (2026-11-06): the design is complete and no application code exists yet.** These documents describe the target system and are the specification for implementation. "Planned" means not yet built. Measured facts are marked as such.

## Start here

| Document | Purpose |
| --- | --- |
| [Requirements](requirements.md) | What the product must do, with stable identifiers and acceptance criteria |
| [Architecture](architecture.md) | Components, invariants, flows and failure domains |
| [Roadmap](roadmap.md) | Phases, tasks and exit checks |
| [Risks](risks.md) | Open questions (none), assumptions and risks |
| [Traceability](traceability.md) | Requirement to design, phase and test |
| [Glossary](glossary.md) | Terms |

## Design

| Document | Purpose |
| --- | --- |
| [Technology choices](technology.md) | Stack, exact versions, licenses and alternatives |
| [Runtime contracts](runtime-contracts.md) | Verified flags and endpoints of KoboldCpp, ComfyUI, Docker and NVML |
| [Model catalog](models.md) | Selectable text and image profiles, defaults per tier, custom files and memory estimates |
| [Data model](data-model.md) | Tables, indexes, full-text search and deletion policy |
| [APIs](api.md) | REST, event WebSocket and control API |
| [Backend](backend.md) | Packages, services, ports and concurrency |
| [Frontend](frontend.md) | Angular structure, routes, state and the ten screens |
| [Design system](design-system.md) | Obsidian Bloom tokens, components and accessibility |

## Subsystems

| Document | Purpose |
| --- | --- |
| [Character system](character-system.md) | Creator, card schema, avatars and exchange format |
| [Memory engine](memory-engine.md) | Layers, extraction, retrieval, contradictions, summaries and context |
| [Consistency engine](consistency-engine.md) | Rules, state manager, versioning, reply normalization, reply checks and the consistency protocol |
| [Prompting and language](prompting.md) | Prompt layout, templates, length bands, samplers, structured output and Italian quality |
| [Image generation](image-generation.md) | Modes, structured scenes, identity and wardrobe, tag vocabulary, workflow and persistence |
| [GPU orchestration](gpu-orchestration.md) | State machine, leases, scheduling, recovery |
| [Proactive messages and time](scheduler.md) | Scheduler, limits and missed events |

## Running and releasing

| Document | Purpose |
| --- | --- |
| [Operations](operations.md) | Install, scripts, Compose, images, backup and troubleshooting |
| [Configuration](configuration.md) | Environment, launch files and settings |
| [Testing](testing.md) | Levels, commands and full test catalog |
| [Security and privacy](security.md) | Threat model, controls and residual risks |
| [Licensing](licensing.md) | Lyrveil, runtimes, models, dependencies and fonts |
| [Publishing](publishing.md) | Repository rules, first push and pre-release audit |

## Records

| Document | Purpose |
| --- | --- |
| [Decisions](decisions/README.md) | One ADR per material decision |
| [Environment, 2026-11-01](verification/2026-11-01-environment.md) | What was measured on the reference machine |
| [Documentation checks, 2026-11-01](verification/2026-11-01-documentation.md) | Checks run on this documentation |
| [Environment and upstream, 2026-11-05](verification/2026-11-05-environment.md) | RAM upgrade, model catalog sources and the Windows-only setup |
| [Consistency and images, 2026-11-06](verification/2026-11-06-consistency-and-images.md) | Upstream facts behind ADRs 0031 to 0036, and the documentation checks |
| [Test command and lint, 2026-11-06](verification/2026-11-06-lint.md) | First runs of `scripts\lyrveil.cmd test lint` and `test unit` |
| [Backend skeleton, 2026-11-06](verification/2026-11-06-backend-skeleton.md) | Backend lock, images, lint gates and unit tests |
| [Backend persistence, 2026-11-06](verification/2026-11-06-backend-persistence.md) | SQLite WAL and FTS5 check, Alembic baseline, health routes and the integration level |
| [Orchestrator state machine, 2026-11-06](verification/2026-11-06-orchestrator-state-machine.md) | Orchestrator lock, test stage, gates and the T-ORC-01 tests of the transition table |
| [Container gate, 2026-11-06](verification/2026-11-06-container-gate.md) | HTTPX in the orchestrator lock, the T-ORC-07 tests of `ContainerGate` and the Docker socket scans of the orchestrator and the backend |
| [Control API, 2026-11-06](verification/2026-11-06-control-api.md) | FastAPI and nvidia-ml-py in the orchestrator lock, the preflight thresholds and the T-ORC-10 tests of the control API token |
| [Frontend shell, 2026-11-06](verification/2026-11-06-frontend-shell.md) | Frontend lock, strict build, lint gates with T-SEC-09, and the first Vitest runs |
| [API types and WebSocket client, 2026-11-06](verification/2026-11-06-api-types-and-socket.md) | Committed OpenAPI document, generated types and their lint check, error mapping and the event socket tests |
| [Core Compose services, 2026-11-06](verification/2026-11-06-compose-core.md) | `docker compose config` and `build` of the three core services, and what has not run |
| [Fake runtimes, 2026-11-06](verification/2026-11-06-fake-runtimes.md) | The fakes of `tests/fakes/`, `compose.test.yaml` and the Docker test of the production images, and what has not run |
| [End-to-end level, 2026-11-06](verification/2026-11-06-e2e-level.md) | The Playwright smoke test against `compose.test.yaml`, the cleanup of the test project, and what has not run |
| [Setup and the docker level, 2026-11-06](verification/2026-11-06-setup.md) | `setup.cmd` run twice against a test project, the preflight advice, the `.env` ACL, T-DCK-06, T-DCK-07 and T-DCK-13 |
