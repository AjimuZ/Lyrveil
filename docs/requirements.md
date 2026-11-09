# Requirements

Status: accepted (2026-11-01), revised 2026-11-05 for the model and hardware changes requested by the owner, and 2026-11-06 for consistency across models and anime image quality. Implementation (2026-11-09): phases P01 to P07 are built and their automated exits passed, and P08 is in progress. The hardware and quality checks of P02 to P06 and the audit of P09 have not run, so a requirement that needs the real GPU or a model's measured quality is not shown to hold yet. [Traceability](traceability.md) and the [roadmap](roadmap.md) give the state per phase.

This document is the normative statement of what Lyrveil must do. Each requirement
has a stable identifier. [Traceability](traceability.md) maps every group to its
design document, roadmap phase and tests.

"Must" is mandatory for version 1.0. "Prepared" means that the design leaves a seam
for later work, and that version 1.0 does not activate it.

## 1. Product

Lyrveil is an open source, simulated messaging application that runs entirely on
the owner's computer. The owner creates AI characters, talks with each of them and
receives generated messages and images.

| ID | Requirement |
| --- | --- |
| R-PRD-01 | The application is a local simulated messenger with AI characters, generated messages and generated images. |
| R-PRD-02 | The experience resembles a modern messaging app, not a technical interface for language models. |
| R-PRD-03 | Each character has its own identity, personality, memory, communication style, look and narrative continuity. |
| R-PRD-04 | Characters can evolve through conversations and stay coherent and independent. |
| R-PRD-05 | The application really works, runs fully in Docker and needs no external AI service. |
| R-PRD-06 | The application does not use SillyTavern as a base. Its architecture and interface are independent. Any reused code respects its license. |

### Non-goals for version 1.0

- Multi-user accounts, remote access and LAN access.
- Group conversations with several characters in one chat.
- Additional AI models for embeddings, summaries, translation, speech or vision.
- Active LoRA, ControlNet or reference-image workflows (prepared).
- Character Card V2 import ([ADR 0021](decisions/0021-character-format-and-card-v2.md)).
- Native mobile apps. Mobile use means a responsive layout.
- An explicit-content mode ([ADR 0018](decisions/0018-adult-only-characters-and-content-safeguards.md)).

## 2. Hardware and environment

| ID | Requirement |
| --- | --- |
| R-HW-01 | The system is optimized for Windows 11, NVIDIA RTX 4070 with 12 GB of VRAM, 32 GB of RAM, Docker Desktop, WSL2 and CUDA. |
| R-HW-02 | The application works offline after the first configuration. |
| R-HW-03 | No commercial API, cloud service or subscription is mandatory. |
| R-HW-04 | Sources live in a Windows folder. Persistent data and models live in Docker volumes, which Docker Desktop keeps on the Linux filesystem of its WSL2 machine, and backups are exported to a Windows folder. Revised on 2026-11-05 at the owner's request ([ADR 0029](decisions/0029-windows-operation-with-docker-volumes.md)). |
| R-HW-05 | Automatic checks cover CUDA compatibility, GPU availability, VRAM, RAM and disk space. |

The tiers are defined in [ADR 0027](decisions/0027-hardware-tiers-after-ram-upgrade.md). The
reference machine was measured at 32 GB on 2026-11-05.

## 3. Technology stack

| ID | Requirement |
| --- | --- |
| R-STK-01 | Frontend: Angular 19 or a later compatible version, TypeScript, standalone components, signals, SCSS, Angular Router, reusable components, modular architecture, WebSocket events, responsive desktop and mobile layouts, zoneless when stable. |
| R-STK-02 | Backend: Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic, SQLite in WAL mode, SQLite FTS5, WebSocket, typed REST APIs, persistent background jobs, asynchronous operations. |
| R-STK-03 | Infrastructure: Docker, Docker Compose, WSL2, NVIDIA CUDA, persistent volumes, health checks, structured logging, Windows management scripts: PowerShell with `.cmd` entry points, revised from Bash on 2026-11-05 at the owner's request. |
| R-STK-04 | AI runtimes: KoboldCpp for text and ComfyUI for images. |
| R-STK-05 | No Redis, PostgreSQL, vector database or other infrastructure service without a concrete technical need. The architecture allows adding one later. |

## 4. Models

| ID | Requirement |
| --- | --- |
| R-MOD-01 | The text model is chosen by the user from a catalog of verified profiles. The default is Gemma 4 26B-A4B Q4_0 on the target tier and Gemma 4 12B Q4_0 on the reduced tier. The catalog also offers Orion 26B-A4B v1.1, Rocinante-X 12B v1 and the brief's original Rocinante 12B v1.1 Q4_K_M. |
| R-MOD-02 | Initial text configuration: CUDA, optimized GPU offload, a context size set per profile (16384 tokens for Gemma 4, 8192 for the Mistral Nemo finetunes), streaming, configurable temperature with a per-profile starting value, the prompt format verified against each model's official template, DRY when available, configurable maximum reply length. |
| R-MOD-03 | Parameters stay compatible with the available VRAM. Context, batch and offload are optimized after measuring real consumption. |
| R-MOD-04 | The image model is chosen by the user from a catalog of SDXL checkpoints of the Illustrious family. The default is Illustrious XL v2.0 from `OnomaAIResearch/Illustrious-XL-v2.0`. The catalog also offers Illustrious XL v1.0 and NoobAI-XL 1.1. |
| R-MOD-05 | Initial image settings: 1024x1024, vertical and horizontal resolutions, batch size 1, configurable seed, steps and CFG, SDXL workflow, positive and negative prompts, stored generation metadata. A second refinement pass at 1.5 times the size is the default quality preset. |
| R-MOD-06 | One text model and one image model are active at a time, and the text model serves every text task. Lyrveil never changes the active model by itself, and no other model is added for embeddings, summaries, translation, upscaling or detection. |
| R-MOD-07 | Modular adapters allow changing models later. A new model in the catalog needs a profile and its verification, not new code, unless it needs a new prompt format. |
| R-MOD-08 | The user can select the active text and image profiles in the model settings, see which catalog files are present, and register a GGUF or SDXL checkpoint already placed in `models/` as an unverified custom profile. The application never downloads models itself. |
| R-MOD-09 | Image quality has two presets: **Alta**, a two-pass render, and **Rapida**, a single pass. The user can choose the default and override it per request. |

## 5. Language

| ID | Requirement |
| --- | --- |
| R-LNG-01 | Italian is the default for the interface, conversations, character descriptions, memories, summaries, notifications, proactive messages, narration and the Character Creator. |
| R-LNG-02 | Replies are natural and colloquial. |
| R-LNG-03 | Characters avoid artificial expressions, repetitive wording and literal translations from English. |
| R-LNG-04 | Each character can have a different communication register. |
| R-LNG-05 | The language is configurable per character or per conversation. |
| R-LNG-06 | Image requests can be written in Italian. When the image model works better with English prompts, the active text model prepares them automatically before leaving the GPU. No external translation service is used. |
| R-LNG-07 | Italian quality is verified with sufficiently long conversations. |
| R-LNG-08 | Italian gender agreement is explicit: each character states how it speaks of itself, and the user states how characters address them. Neither is guessed from a name. |

## 6. Messaging

| ID | Requirement |
| --- | --- |
| R-MSG-01 | The main interface looks like an app with contacts and conversations. It does not resemble ChatGPT, ComfyUI or a developer dashboard. |
| R-MSG-02 | The chat list shows avatar, contact name, last message, last message time, unread count and typing indicator. |
| R-MSG-03 | The chat list supports search, sorting by activity, pinning, archiving, deletion and creating conversations. |
| R-MSG-04 | A conversation supports text messages, images, messages with both, timestamps and visual day separators. |
| R-MSG-05 | A conversation shows a generation indicator, a simulated read state and streaming replies. |
| R-MSG-06 | A conversation supports editing messages, regenerating replies, alternative replies, deleting messages, searching in the chat and full-screen images. |
| R-MSG-07 | History is persistent. |
| R-MSG-08 | Images are real messages with an identifier, timestamp, author and metadata. Showing them never requires ComfyUI to be running. |
| R-MSG-09 | A character can have several different conversations. |
| R-MSG-10 | Each conversation has a unique identifier, history, state, episodic memory, optional scenario, its own relationship and narrative context. |
| R-MSG-11 | Conversations stay isolated by default. |

## 7. Character Creator

| ID | Requirement |
| --- | --- |
| R-CHR-01 | The user can create, edit, duplicate, import, export and delete characters. |
| R-CHR-02 | Identity fields: first name, optional last name, age, grammatical gender, narrative identity, biography, occupation, interests, background, place or setting, main language. |
| R-CHR-03 | Personality fields: main traits, values, motivations, preferences, flaws, fears, habits, sense of humor, temperament, way of relating to others. |
| R-CHR-04 | Communication fields: formality, average message length, question frequency, punctuation use, colloquial style, expressiveness, initiative frequency, address form (*tu* or *Lei*), narration, emoji use, message examples as exchanges, recurring expressions, phrases to avoid. |
| R-CHR-05 | Characters are not reduced to numbers. Descriptive fields take priority. |
| R-CHR-06 | Visual profile fields: face, hair, eyes, body, clothing, accessories, distinctive marks, art style, avatar, reference images, persistent prompt as a structured English identity, usual outfit and named outfits. |
| R-CHR-07 | The user can describe a character in natural language. The active text model generates a structured sheet that is validated by a Pydantic schema and shown in the Creator. |
| R-CHR-08 | The user can edit any generated field before saving. |
| R-CHR-09 | Avatars: upload a local image, generate with the active image model, regenerate, choose a crop, save several variants, set a primary image. |
| R-CHR-10 | The Creator uses the same image infrastructure as conversations. |
| R-CHR-11 | Import and export use a documented, versioned JSON format. The proprietary format is never a mandatory dependency. |
| R-CHR-12 | Character Card V2 compatibility is evaluated later ([ADR 0021](decisions/0021-character-format-and-card-v2.md)). |

## 8. Memory: Lyrveil Memory Engine

| ID | Requirement |
| --- | --- |
| R-MEM-01 | Characters remember significant information, experiences, preferences, events and changes in the relationship. |
| R-MEM-02 | Memory survives application restarts, LLM container stops, model reloads, image generation, very long conversations and switching between chats. It never relies on the model staying in the GPU. |
| R-MEM-03 | Character Identity Memory is the canonical definition. A normal conversation never changes it automatically. Changes are versioned and controlled by the user. |
| R-MEM-04 | Character Shared Memory holds information optionally shared across conversations of one character. It is configurable, and private memories are not shared by default. |
| R-MEM-05 | Conversation Memory holds what is learned in one chat and always links to its source conversation. |
| R-MEM-06 | Episodic Memory holds significant events with identifier, title, description, timestamp, participants, source conversation, source messages, importance and links to other episodes. |
| R-MEM-07 | Relationship Memory records the development of the relationship in one conversation: familiarity, trust, confidence, tensions, important events, expectations, stated limits and preferences, and evolution. It holds mainly events and descriptions, not only scores. |
| R-MEM-08 | Working Memory is rebuilt dynamically for each message from recent messages, current situation, current topic, relevant memories, goals and pending events. |
| R-MEM-09 | The full history stays in the database. Structured memory holds only useful information. |
| R-MEM-10 | Every 8 to 12 messages, or at significant events, an analysis uses the already loaded text model and produces validated structured JSON. |
| R-MEM-11 | Each memory has at least ID, character ID, conversation ID or shared scope, type, content, timestamp, importance, confidence, source, status, optional expiry and version. |
| R-MEM-12 | The system distinguishes stated facts, model inferences, fictional narrative elements, temporary information, contradicted information and memories confirmed by the user. |
| R-MEM-13 | Inferences never become certain facts automatically. Uncertain interpretations never change canonical data. |
| R-MEM-14 | A failed extraction loses no message. The procedure is repeatable and idempotent. |
| R-MEM-15 | Retrieval uses SQLite FTS5 and combines full-text search, BM25, importance, recency, relation to the current topic, memory type, validity status and conversation scope. |
| R-MEM-16 | A retrieval function returns a limited number of relevant memories within a token budget. The whole memory database never enters the prompt. |
| R-MEM-17 | No embedding model is added in the first version. A `MemoryRetriever` interface allows semantic or hybrid search later. |
| R-MEM-18 | The system distinguishes new information, updates of an earlier fact, contradictions, temporary information and explicit user corrections. |
| R-MEM-19 | On a conflict the system detects it, keeps the history, updates the fact's status, keeps references to source messages and uses the temporally correct version. It never silently deletes history. |
| R-MEM-20 | When a contradiction cannot be resolved with certainty, the uncertainty is preserved. |
| R-MEM-21 | Long conversations get progressive, versioned checkpoints, not one overwritten summary. |
| R-MEM-22 | Each checkpoint holds the message range, relevant events, decisions, relationship evolution, unresolved situations, important information and references to source messages. |
| R-MEM-23 | Summaries never replace the original history. They rebuild context beyond the available window. They are never produced while the image model loads. |
| R-MEM-24 | A `ContextBuilder` composes global instructions, canonical identity, communication profile, conversation state, relevant memories, contextual summary, recent messages and the current message. |
| R-MEM-25 | The token budget is configurable. The initial 8192-token window uses an adaptive split that leaves enough room for the reply. |
| R-MEM-26 | Tokens are counted with the runtime tokenizer. Character estimates are never the only criterion. |
| R-MEM-27 | Over budget, the least relevant material is reduced first. Identity and fundamental instructions are not removed indiscriminately. |

## 9. Character Consistency Engine

| ID | Requirement |
| --- | --- |
| R-CON-01 | Canonical identity prevails over uncertain memories. |
| R-CON-02 | A character does not automatically know information from other chats. |
| R-CON-03 | A relationship does not change drastically because of one message. |
| R-CON-04 | Past events are respected, and earlier decisions can influence replies. |
| R-CON-05 | Personality evolves gradually and with a reason. |
| R-CON-06 | Persistent visual details do not change at random. |
| R-CON-07 | A Character State Manager stores one state per conversation: contextual emotional state, familiarity, trust, confidence, recent events, unresolved situations, current goals and narrative changes. |
| R-CON-08 | Values are not regenerated at random. Updates derive from identifiable events, are limited and are recorded. |
| R-CON-09 | Numeric scores, when present, are internal support and do not replace the narrative description. |
| R-CON-10 | Each relevant character change is historicized. The user can view versions, compare changes, restore a version, lock traits and correct information. |
| R-CON-11 | A character keeps the same name, age, gender, address form, narration style and reply length on every verified text profile, and after a profile change in the middle of a conversation. Code enforces what can be checked, and does not rely on the model alone. |
| R-CON-12 | Every catalog text profile passes a consistency protocol and an extraction evaluation before it is offered as verified. The evidence is recorded. |

## 10. Character Memory Studio

| ID | Requirement |
| --- | --- |
| R-STU-01 | An interface section shows what each character remembers: memories by category, search, filters, history, provenance, editing, deletion, locking, contradiction correction, events, relationship state and conversation summaries. |
| R-STU-02 | A memory deleted by the user cannot be recreated immediately from the same source without new information. |
| R-STU-03 | When earlier messages are edited or deleted, derived memories and summaries are re-evaluated or invalidated. |
| R-STU-04 | An optional diagnostic mode shows which memories entered the prompt of a given reply. It is not visible in the normal chat experience. |

## 11. Image generation

| ID | Requirement |
| --- | --- |
| R-IMG-01 | Characters can send images as normal messages. |
| R-IMG-02 | Manual mode: the user asks for an image in the chat, the backend interprets the request and generates it through ComfyUI, and the result appears in the conversation. |
| R-IMG-03 | Contextual mode: a character can propose an image coherent with the conversation. It is configurable and disabled initially. Images are never generated automatically after each message. The decision is a structured event validated by the backend. |
| R-IMG-04 | Character mode: avatars, profile images, set illustrations, contextual scenes and character variants. |
| R-IMG-05 | A visual PromptBuilder combines the visual profile, persistent visual identity, setting, message content, art style, positive prompt, negative prompt and checkpoint parameters. |
| R-IMG-06 | The text model produces the structured prompt before leaving the GPU. |
| R-IMG-07 | One character keeps hair, eyes, physical features, accessories, distinctive marks, graphic style and recurring clothing when relevant. |
| R-IMG-08 | Seed, reference images and persistent prompts can be saved. The architecture is prepared for LoRA and compatible reference techniques. The documentation never promises perfect consistency from the seed alone. |
| R-IMG-09 | Each image is stored independently of the AI runtime: original file, optional thumbnail, message ID, character ID, conversation ID, prompt, seed, parameters, generation date, model profile, checkpoint hash, quality preset and workflow. |
| R-IMG-10 | The backend serves images even when ComfyUI is off. |
| R-IMG-11 | A character's visual identity, outfit and apparent age band reach every prompt as deterministic tags, identical whichever text model is active. |
| R-IMG-12 | Model output reaches the image prompt only through a structured scene, a tag vocabulary and a sanitizer, so it cannot change prompt weights, load embeddings or override identity. |
| R-IMG-13 | The text that accompanies an image is written in the character's voice, with the same rules as a normal reply. |

## 12. GPU orchestration: Model Lifecycle Manager

| ID | Requirement |
| --- | --- |
| R-GPU-01 | The text model and the image model are never loaded in the GPU at the same time. Management is automatic. |
| R-GPU-02 | Normal state: frontend, backend, orchestrator and KoboldCpp active, the text model loaded, ComfyUI inactive or without GPU allocations. |
| R-GPU-03 | An image request follows the 18 steps mapped in [GPU orchestration](gpu-orchestration.md#the-18-steps-of-an-image-request). |
| R-GPU-04 | A state machine implements at least IDLE, LOADING_LLM, LLM_READY, GENERATING_TEXT, UNLOADING_LLM, LOADING_IMAGE, IMAGE_READY, GENERATING_IMAGE, UNLOADING_IMAGE, RECOVERING and ERROR, with explicit validated transitions. |
| R-GPU-05 | Reliability: global GPU lock, persistent job queue, configurable priorities, concurrent request handling, timeouts, limited retries, job cancellation, NVML monitoring, detection of processes actually active, CUDA OOM handling, error recovery, safe shutdown and transition logs. |
| R-GPU-06 | GPU memory verification does not rely only on total free memory, because other programs can use the card. It also checks containers and managed processes. |
| R-GPU-07 | Processes outside Lyrveil are never terminated. |
| R-GPU-08 | `docker compose down` is not used for model switches. Core application services stay available. |
| R-GPU-09 | A text request during image generation is saved immediately, queued, shown with an understandable status and executed when the text model returns. |
| R-GPU-10 | No second model instance starts to answer concurrently. Requests from different conversations are handled correctly. |

## 13. Proactive messages and time

| ID | Requirement |
| --- | --- |
| R-SCH-01 | Characters can send messages without the user's initiative. This is configurable and disabled by default. |
| R-SCH-02 | A local scheduler supports scheduled messages, configurable frequency, time windows, inactivity periods, follow-up messages, contextual initiatives and in-app notifications. |
| R-SCH-03 | Frequency has configurable limits that prevent repetitive or excessive messages. |
| R-SCH-04 | Proactive messages respect the character's personality and memory, and are never random phrases. |
| R-SCH-05 | The backend provides local time, date, last interaction, elapsed time, planned events and conversations left open. |
| R-SCH-06 | The scheduler runs only when local services are active. |
| R-SCH-07 | While the PC is off, no AI generation is simulated retroactively. At restart, missed events follow an explicit configurable policy. |

## 14. Design system: Obsidian Bloom

| ID | Requirement |
| --- | --- |
| R-UI-01 | The style combines modern minimalism, dark aesthetics, JRPG references, discreet pixel art, contemporary fantasy atmosphere and a familiar messaging experience. It is not a technical dashboard. |
| R-UI-02 | The palette is the one in [Design system](design-system.md#color-tokens). |
| R-UI-03 | Dark mode by default, clean layout, thin borders, slightly rounded corners, discreet shadows, short animations and careful microinteractions. No excessive neon, invasive glassmorphism, bulky pixel decoration or arcade effects. |
| R-UI-04 | Messages use readable fonts. Pixel fonts are reserved for short titles, small labels and graphic details. |
| R-UI-05 | Screens: conversation list, conversation, Character Creator, Character Profile, Character Memory Studio, image gallery, scenario management, general settings, model settings and AI resource monitoring. |
| R-UI-06 | The technical section is reached from the settings and does not occupy the main screen. |
| R-UI-07 | Desktop: contact sidebar, central conversation area and a collapsible context panel. |
| R-UI-08 | Mobile: separate chat list, full-width conversation, simple navigation, fixed composer and secondary panels on request. |
| R-UI-09 | Keyboard accessibility, focus management, loading states and respect for reduced-motion preferences. |

## 15. Backend architecture

| ID | Requirement |
| --- | --- |
| R-BE-01 | The backend has separate modules and the services listed in [Backend](backend.md#services). No monolithic classes. |
| R-BE-02 | Application domain, database, AI runtimes and HTTP API are separated. |
| R-BE-03 | Dependency injection and abstract interfaces are used where really useful. Services do not depend on the Angular interface. |
| R-BE-04 | The schema has at least the tables listed in [Data model](data-model.md), with foreign keys, indexes, uniqueness constraints and deletion policies. |
| R-BE-05 | Alembic manages migrations. |
| R-BE-06 | No indiscriminate cascading deletes that could remove data the user wants to keep. |
| R-BE-07 | Backup, export and restore are provided. |

## 16. Docker and startup

| ID | Requirement |
| --- | --- |
| R-OPS-01 | The application is fully dockerized with the services `lyrveil-frontend`, `lyrveil-backend`, `lyrveil-orchestrator`, `lyrveil-llm` and `lyrveil-image`. |
| R-OPS-02 | The first three stay active. AI services run only when needed. |
| R-OPS-03 | The Docker socket is not exposed to untrusted services. The lifecycle manager controls only Lyrveil containers, through reduced authority and an explicit allowlist. Residual risks are documented. |
| R-OPS-04 | Administrative ports are not published on the external network. |
| R-OPS-05 | The root has `start.cmd`, `stop.cmd`, `restart.cmd`, `status.cmd`, `logs.cmd` and `setup.cmd`. The main command is `.\start.cmd`. The brief's `.sh` names were replaced on 2026-11-05 at the owner's request. |
| R-OPS-06 | `start.cmd` verifies Docker, Compose, GPU support, persistent volumes and models, runs setup if needed, starts services, verifies health checks, starts the text model and shows the local URL `http://localhost:4200`. |
| R-OPS-07 | Scripts are idempotent. Normal restarts and updates never delete persistent data. |
| R-OPS-08 | A script downloads the default models for the machine's tier, and any other catalog model on request, to the right directories: only the needed files, resumable, verified for integrity and size, no re-download of valid files, network errors handled, required space shown, weights never inside Docker images. Downloads are separate from the build. |

## 17. Publication

| ID | Requirement |
| --- | --- |
| R-PUB-01 | The project can be published as a public repository named `Lyrveil`. It holds only code, example configuration, documentation, tests and reusable original assets. |
| R-PUB-02 | The repository excludes weights, real databases, conversations, personal memories, user images, personal references, real `.env` files, tokens, passwords, caches, logs, backups, temporary files, Docker volumes and local user data. |
| R-PUB-03 | `.gitignore` and `.dockerignore` are configured before the first commit. |
| R-PUB-04 | Licenses of KoboldCpp, ComfyUI, Python and Angular dependencies, every catalog model, graphic assets and fonts are verified and documented with attribution duties. Weights are never distributed. The code license is compatible with what is actually distributed. |
| R-PUB-05 | The public README has description, demo screenshots, features, architecture, hardware requirements, installation, configuration, start with `start.cmd`, supported models, how memory works, how images work, limitations, troubleshooting, licenses and acknowledgements. |
| R-PUB-06 | Only original demo characters and synthetic data are used. |
| R-PUB-07 | Commits are readable and progressive. Unverified code is not pushed. |
| R-PUB-08 | Before publication: tests, dependency check, secret scan, large-file check, personal-data check, history check, license check, clean build, then publication with the GitHub CLI when authenticated. Otherwise the local repository stays ready and the necessary steps are documented. |
| R-PUB-09 | Private data never enters the Git history, not even temporarily. |

## 18. Tests

| ID | Requirement |
| --- | --- |
| R-TST-01 | Unit, integration and end-to-end tests exist. |
| R-TST-02 | Character Creator tests cover creation, editing, duplication, export, import, persistence, versioning and avatar generation. |
| R-TST-03 | Memory tests cover extraction, deduplication, retrieval, editing, deletion, contradictions, facts versus inferences, conversation isolation, progressive summaries, recovery after restart and invalidation after message edits. |
| R-TST-04 | Consistency tests use several characters: no automatic knowledge from another character, no state leakage between independent chats, no canonical change without an authorized operation. |
| R-TST-05 | Messaging tests cover chat creation, sending, streaming, regeneration, editing, deletion, page reload, history recovery and several simultaneous conversations. |
| R-TST-06 | Image tests cover prompt creation, GPU alternation, ComfyUI start, real generation, saving, insertion in the chat, text model recovery, persistence after restart and regeneration. |
| R-TST-07 | Docker tests cover `start.cmd`, `stop.cmd`, restart, volume persistence, health checks, GPU passthrough, absence of orphan processes and CUDA error handling. |
| R-TST-08 | Orchestration logic tests run without a GPU through simulated runtimes. Real GPU tests run when the hardware is available. Tests not executed are never declared passed. |

## 19. Delivery and engineering rules

| ID | Requirement |
| --- | --- |
| R-ENG-01 | Development proceeds in the coherent phases of the [roadmap](roadmap.md). Each phase leaves the project coherent and startable. |
| R-ENG-02 | Implementation first delivers a complete working path from character creation to persistent conversation, then advanced memory, images and GPU orchestration. |
| R-ENG-03 | The rules in [CONTRIBUTING](../CONTRIBUTING.md#engineering-rules) apply: real environment analysis, official documentation, no invented runtime endpoints, no stubs presented as working, no mock data in production paths, no unnecessary frameworks, no cloud dependencies, no extra AI services, no model replacement without authorization, no weakened persistence, no requirement declared complete without verification. |
| R-ENG-04 | Technical documentation and architectural decisions stay up to date during development. |

## 20. Safety requirements added by design

The original brief does not list these. They follow from the model licenses and
from the nature of the product. See
[ADR 0018](decisions/0018-adult-only-characters-and-content-safeguards.md).

| ID | Requirement |
| --- | --- |
| R-SAF-01 | Every character is an adult. Age is required and at least 18 at creation, import and generation. |
| R-SAF-02 | No path produces sexual content involving minors. Deterministic checks run on character data, image prompts and generated text, and settings cannot disable them. |
| R-SAF-03 | The image pipeline always applies its safety negative prompt. Version 1.0 has no explicit-content mode. |
| R-SAF-04 | The application states clearly that characters are simulated. A character does not deny it when the user sincerely asks. |
| R-SAF-05 | Proactive messages never pressure the user or induce guilt for absence. |
| R-SAF-06 | When the user appears to be in danger or talks about self-harm, the character leaves the fiction gently and encourages real help. |

## 21. Acceptance criteria

Version 1.0 is accepted when a user can do all of the following, and each item has
dated evidence in `docs/verification/`:

| ID | Criterion |
| --- | --- |
| AC-01 | Clone the repository. |
| AC-02 | Install the required models through setup. |
| AC-03 | Start the application with `.\start.cmd`. |
| AC-04 | Open Lyrveil in the browser. |
| AC-05 | Create a character. |
| AC-06 | Define its look and personality. |
| AC-07 | Generate or upload an avatar. |
| AC-08 | Start a conversation in Italian. |
| AC-09 | Receive replies coherent with the character. |
| AC-10 | Create other conversations and other characters. |
| AC-11 | Resume an earlier conversation without losing memories. |
| AC-12 | View and correct the character's memory. |
| AC-13 | Ask for an image in the chat. |
| AC-14 | See the image appear as a message. |
| AC-15 | Keep chatting after the text model is restored automatically. |
| AC-16 | Close and reopen the application without losing data. |
| AC-17 | Configure proactive messages. |
| AC-18 | Use everything offline. |
| AC-19 | Verified: the text model and the image model are never held in VRAM at the same time. |
| AC-20 | Verified: the public repository is reproducible and holds no private data or model weights. |
