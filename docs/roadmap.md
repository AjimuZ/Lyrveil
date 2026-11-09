# Roadmap

Each phase leaves the project coherent and startable. Check an item only after its exit checks pass, and link dated evidence from `docs/verification/`.

| Phase | Result | State |
| --- | --- | --- |
| P00 | Complete design documentation | Done |
| P01 | Foundations: empty app starts, stops and restarts cleanly | Done |
| P02 | A real streaming chat with the default text profile | In progress: automated exit passed; hardware part (P02.7, P02.EXIT-HW) pending |
| P03 | Characters: Creator, profiles, avatars by upload, import and export | In progress: automated exit passed; hardware and manual part (P03.EXIT-HW) pending |
| P04 | Memory and consistency | In progress: automated exit passed; quality part (P04.10) pending |
| P05 | Images in messages | In progress: automated exit passed; hardware part (P05.EXIT-HW) and measurement part (P05.8) pending |
| P06 | Automatic GPU orchestration and recovery | In progress: automated exit passed; hardware part (P06.EXIT-HW) pending |
| P07 | Proactive messages and messaging details | Done |
| P08 | Obsidian Bloom polish, responsive layout, accessibility | Planned |
| P09 | Audit and public release | Planned |

Through P03, the project delivers the complete working path from character creation to persistent conversation. Later phases add advanced memory, images and orchestration on top of it.

## P00: Documentation

**Depends on:** nothing. **Areas:** docs, repository hygiene.

- [x] P00.1 Examine the environment and record measurements.
- [x] P00.2 Verify runtimes, models and licenses at official sources.
- [x] P00.3 Write requirements, design, operations and test documents.
- [x] P00.4 Record decisions as ADRs. Leave no open question.
- [x] P00.5 Configure `.gitignore`, `.dockerignore` and `.gitattributes` before the first commit.
- [x] P00.6 Check links, placeholders, requirement coverage and secrets.
- [x] P00.7 Revise for the 32 GB RAM upgrade: selectable model profiles, prompt templates per model family, two-pass
  image rendering, new hardware tiers and the KoboldCpp `v1.124.2` pin (ADRs 0024 to 0028).
- [x] P00.8 Revise for operation from Windows on `C:` with Docker volumes, after measuring storage speed, and record the
  KoboldCpp efficiency levers that P02 measures (ADRs 0029 and 0030).
- [x] P00.9 Revise for characters that stay consistent on every text profile, and for anime image quality. The revision
  covers six areas (ADRs 0031 to 0036):
  - a stable prompt prefix with an anchor before each reply;
  - the reply normalizer and the identity checks;
  - the voice fields;
  - the consistency and extraction protocols;
  - a structured visual identity with a tag vocabulary;
  - structured scenes and captions in the character's voice.

Exit: every requirement maps to a design document, a phase and a test. Link and placeholder checks pass. No private data or weights in the repository.

Evidence:

- [environment](verification/2026-11-01-environment.md);
- [documentation checks](verification/2026-11-01-documentation.md);
- [2026-11-05 revision](verification/2026-11-05-environment.md);
- [2026-11-06 revision](verification/2026-11-06-consistency-and-images.md).

## P01: Foundations

**Depends on:** P00. **Areas:** repository, Docker, backend, orchestrator, frontend.

- [x] P01.1 Apply the `.wslconfig` for the machine's tier. On the reference machine the 32 GB values were applied on 2026-11-05, and Docker sees 23.47 GiB ([evidence](verification/2026-11-05-environment.md#after-the-changes)). The P01 exit check confirms it.
- [x] P01.2 Create the repository structure, `.env.example`, the `.gitattributes` rules for `.cmd` and `.ps1`, and `compose.yaml` with the three core services and the external volumes.
- [x] P01.3 Backend skeleton: FastAPI, settings, structured logging, SQLite with verified WAL and FTS5, Alembic baseline, health routes.
- [x] P01.4 Orchestrator skeleton: state machine table, `ContainerGate`, `GPUResourceMonitor`, control API with token, preflight.
- [x] P01.5 Frontend skeleton: Angular 22 zoneless shell, routes, tokens, fonts, API types, WebSocket client.
- [x] P01.6 Scripts: `setup.cmd`, `start.cmd`, `stop.cmd`, `restart.cmd`, `status.cmd`, `logs.cmd`, and `scripts\lyrveil.ps1` with `models` driven by the model catalog, `import-model`, `backup`, `restore` and `test`.
- [x] P01.7 Security baseline: Host and Origin checks, headers, internal networks, non-root containers.
- [x] P01.8 Fakes, CI, lint and type gates. CI also checks documentation links, anchors, requirement coverage and Mermaid diagrams.

Exit: T-ORC-01, T-ORC-07, T-ORC-10, T-SEC-01, T-SEC-02, T-SEC-09, T-DCK-01 to T-DCK-11, T-DCK-13 pass. `.\start.cmd` opens an empty app at `http://localhost:4200`, and `status.cmd` reports it. FTS5 and WAL are verified in the real image.

Evidence:

- [WSL2 memory](verification/2026-11-06-wsl-memory.md);
- [backend skeleton and gates](verification/2026-11-06-backend-skeleton.md);
- [backend persistence and health routes](verification/2026-11-06-backend-persistence.md);
- [orchestrator package and state machine](verification/2026-11-06-orchestrator-state-machine.md);
- [container gate](verification/2026-11-06-container-gate.md);
- [control API, GPU monitor and preflight](verification/2026-11-06-control-api.md);
- [P01 exit: the `unit`, `integration`, `e2e`, `docker` and `lint` levels, WAL and FTS5 in the real image](verification/2026-11-07-p01-exit.md). `.\start.cmd` and `status.cmd` on the real project at `http://localhost:4200` were not run there; the `docker` level ran the same code on a test project.

## P02: Working chat

**Depends on:** P01. **Areas:** LLM runtime, messaging.

- [x] P02.1 `docker/llm` image from the pinned binary. Confirm the unpacked launcher and read-only root.
- [x] P02.1a `config/model-catalog.json`, its validator and the tier defaults, including `swa_padding`, `smart_cache`, `quality_evidence` and the `generic` preset.
- [x] P02.2 Orchestrator loads and stops the LLM. Leases.
- [x] P02.3 `KoboldCppRuntime`: stream, generate, token count, abort. `PromptFormatter` for `gemma4`, `mistral_v3_tekken` and `chatml`, with the stable prefix, the frame of the dynamic context and the anchor ([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)).
- [x] P02.4 Job queue with persistence and retries.
- [x] P02.5 Conversations, messages, variants, streaming over WebSocket, search.
- [x] P02.6 Chat list and conversation screens with a built-in minimal character form.
- [ ] P02.7 Run the [VRAM tuning procedure](gpu-orchestration.md#measuring-and-tuning) with the speed rule and the [efficiency levers](decisions/0030-koboldcpp-efficiency-levers.md). Record the numbers and update the catalog's VRAM values. Apply the [default rule](models.md#defaults-and-how-they-can-change).

Exit: T-MSG-01 to T-MSG-09, T-MSG-11 to T-MSG-13, T-ORC-03, T-SEC-03, T-SEC-06, T-MOD-01, T-MOD-02 and T-MOD-05 pass. T-GPU-01 to T-GPU-04 and T-GPU-14 pass on the reference machine. A long Italian chat with the default text profile survives a page reload and an application restart.

Evidence:

- [P02 automated exit: the `unit`, `integration`, `e2e` and `lint` levels, with the tests named by the exit](verification/2026-11-07-p02-exit.md). The `docker` level was not run there, and the hardware part (T-GPU-01 to T-GPU-04, T-GPU-14, the long Italian chat across a reload and a restart) is pending with P02.7 and P02.EXIT-HW, so the phase is not done.

## P03: Characters

**Depends on:** P02. **Areas:** Character Creator, profile, assets, data.

- [x] P03.1 Full card, traits, communication and visual fields, versions, diff, restore and locks. The card includes the
  voice fields of [ADR 0033](decisions/0033-card-fields-for-a-model-independent-voice.md), and the identity slots and wardrobe
  of [ADR 0035](decisions/0035-structured-visual-identity-and-wardrobe.md).
- [x] P03.2 Creator screens with Signal Forms, profile screen, duplicate, archive and purge.
- [x] P03.3 Assisted creation with grammar, validation and repair.
- [x] P03.4 Upload, crop, variants and primary avatar.
- [x] P03.5 Exchange format, import, export and JSON Schema.
- [x] P03.6 Content guard for character data. Demo characters.
- [x] P03.7 Multiple conversations per character, scenarios, backup and restore.

Exit: T-CHR-01 to T-CHR-11, T-CHR-13, T-CHR-14, T-SEC-04, T-SEC-05, T-SEC-07, T-SEC-08, T-DCK-12 pass. T-GPU-05 passes. AC-01 to AC-06, AC-08 to AC-10 and AC-16 hold in a manual run, with an uploaded avatar.

Evidence:

- [P03 automated exit: the `unit`, `integration`, `e2e`, `docker` and `lint` levels, with the tests named by the exit, T-DCK-12 included](verification/2026-11-07-p03-exit.md). The hardware and manual part (T-GPU-05, and AC-01 to AC-06, AC-08 to AC-10 and AC-16 in a manual run with an uploaded avatar) is pending with P03.EXIT-HW, so the phase is not done.

## P04: Memory

**Depends on:** P03. **Areas:** memory engine, consistency engine, Memory Studio.

- [x] P04.1 Memory tables, FTS5, stemming and stop-word lists.
- [x] P04.2 Extraction pipeline with idempotency, consistency rules and tombstones.
- [x] P04.3 `FtsMemoryRetriever` and scoring.
- [x] P04.4 Progressive summaries and rollups.
- [x] P04.5 Full `ContextBuilder` with runtime token counts, length bands and the anchor.
- [x] P04.6 `CharacterStateManager`, bounded updates, replay.
- [x] P04.7 Invalidation and re-evaluation.
- [x] P04.8 Reply normalizer, reply checks V1 to V13 with the corrective generation, and adaptive notes ([ADR 0032](decisions/0032-model-independent-reply-normalization.md)).
- [x] P04.9 Memory Studio, including diagnostic mode.
- [ ] P04.10 Run the Italian quality protocol, the consistency protocol and the extraction evaluation on the catalog
  profiles, with the SWA padding and SmartCache measurements. Apply the verified-profile rule and record
  `quality_evidence` ([ADR 0034](decisions/0034-character-consistency-protocol-and-verified-profiles.md)).

Exit: T-MEM-01 to T-MEM-23, T-CON-01 to T-CON-06, T-CON-08 to T-CON-11 pass. T-QLT-01 meets its targets or records the tuning and repeat. T-QLT-05 and T-QLT-06 meet their hard targets on every profile that stays in the catalog. AC-11 and AC-12 hold.

Evidence:

- [P04 automated exit: the `unit`, `integration`, `e2e` and `lint` levels, with T-MEM-01 to T-MEM-23, T-CON-01 to T-CON-06 and T-CON-08 to T-CON-11, and AC-11 and AC-12 through their tests](verification/2026-11-07-p04-exit.md). The `docker` level was not run there. The quality part (T-QLT-01, T-QLT-05, T-QLT-06 on the catalog profiles) is pending with P04.10, so the phase is not done.

## P05: Images

**Depends on:** P04. **Areas:** ComfyUI, visual pipeline, gallery.

- [x] P05.1 `docker/image` from the pinned commit, with a lock file.
- [x] P05.2 `ComfyUiRuntime`, workflow templates for **Alta** and **Rapida** and their tiled variants, and progress for both passes.
- [x] P05.3 `VisualConsistencyService`, the tag vocabulary with token counts, the sanitizer, structured profile translation and the scene filter ([ADR 0035](decisions/0035-structured-visual-identity-and-wardrobe.md)).
- [x] P05.4 `image_request` job with `ImageScene`, the caption in the character's voice and aspect `auto`, persistence, thumbnails and message integration ([ADR 0036](decisions/0036-structured-image-scenes-and-measured-levers.md)).
- [x] P05.5 Manual mode, detector, contextual mode switched off, character mode and avatar generation.
- [x] P05.6 Gallery.
- [x] P05.7 Mode switching through the orchestrator's normal path, with exclusivity enforced. Full automation and recovery follow in P06.
- [ ] P05.8 Measure image VRAM and time per preset. Update defaults. Run the parameter grid for each image profile, the image identity protocol and the measured levers: PAG, identity weight, and the tall and wide sizes. Apply the preset rule and the default rule.

Exit: T-CHR-12, T-CON-07, T-IMG-01 to T-IMG-03, T-IMG-05 to T-IMG-09, T-IMG-13 to T-IMG-18 pass. T-GPU-01, T-GPU-06, T-QLT-02 and T-QLT-07 pass. AC-07, AC-13 and AC-14 hold.

Evidence:

- [P05 automated exit: the `unit`, `integration`, `e2e`, `docker` and `lint` levels, with T-CHR-12, T-CON-07, T-IMG-01 to T-IMG-03, T-IMG-05 to T-IMG-09 and T-IMG-13 to T-IMG-18 (the unit part of T-IMG-14)](verification/2026-11-07-p05-exit.md). The hardware part (T-GPU-01, T-GPU-06, AC-07, AC-13, AC-14) is pending with P05.EXIT-HW, and the measurement and quality part (the real-GPU part of T-IMG-14, T-QLT-02, T-QLT-07) is pending with P05.8, so the phase is not done.

## P06: Orchestration

**Depends on:** P05. **Areas:** Model Lifecycle Manager, job scheduling, resources screen.

- [x] P06.1 Complete the 18-step flow with all states.
- [x] P06.2 Release verification, VRAM guards and blocked states.
- [x] P06.3 Scheduling rules, batching, fairness, preemption and cancellation.
- [x] P06.4 Launch levels, out-of-memory handling and recovery table.
- [x] P06.5 Safe shutdown, drain and event persistence.
- [x] P06.6 AI resources screen and model settings, with profile selection, **Applica ora** and custom registration.

Exit: T-IMG-04, T-IMG-10 to T-IMG-12, T-MOD-03, T-MOD-04, T-DCK-14, T-ORC-02, T-ORC-04 to T-ORC-06, T-ORC-08, T-ORC-09, T-ORC-11 to T-ORC-13 pass. T-GPU-07 to T-GPU-12 pass. AC-15, AC-18 and AC-19 hold, with measured switch times recorded.

Evidence:

- [P06 automated exit: the `unit`, `integration`, `e2e`, `docker` and `lint` levels, with T-IMG-04, T-IMG-10 to T-IMG-12, T-MOD-03, T-MOD-04, T-DCK-14, T-ORC-02, T-ORC-04 to T-ORC-06, T-ORC-08, T-ORC-09 and T-ORC-11 to T-ORC-13](verification/2026-11-08-p06-exit.md). The hardware part (T-GPU-07 to T-GPU-12, AC-15, AC-18, AC-19 and the measured switch times) is pending with P06.EXIT-HW, so the phase is not done.

## P07: Advanced messaging

**Depends on:** P06. **Areas:** scheduler, notifications, messaging details.

- [x] P07.1 Scheduled events, evaluation and limits.
- [x] P07.2 Decide and generate stages, with the repetition check.
- [x] P07.3 Missed-event policies.
- [x] P07.4 Time context in prompts.
- [x] P07.5 Notifications.
- [x] P07.6 Read state, unread counts and final messaging details.

Exit: T-MSG-10 and T-SCH-01 to T-SCH-07 pass. AC-17 holds.

Evidence:

- [P07 exit: the `unit`, `integration`, `e2e` and `lint` levels, with T-MSG-10, T-SCH-01 to T-SCH-07 and AC-17 through T-SCH-01 to T-SCH-03](verification/2026-11-09-p07-exit.md). The `docker` level did not finish and is not part of this exit; two `e2e` failures in earlier runs were not explained and did not recur.

## P08: Polish

**Depends on:** P07. **Areas:** design system, accessibility, performance, documentation.

- [x] P08.1 Complete Obsidian Bloom: primitives, icons, motion, empty and loading states.
- [ ] P08.2 Responsive layouts for desktop, tablet and mobile.
- [ ] P08.3 Keyboard, focus and live regions.
- [ ] P08.4 Performance: long histories, large galleries and prompt prefix stability.
- [ ] P08.5 Demo screenshots from the demo characters and synthetic data.
- [ ] P08.6 Update all documents to match delivered behavior.

Exit: T-QLT-01, T-QLT-03, T-QLT-04, T-QLT-05 and T-GPU-13 pass. No default profile lacks `quality_evidence`. No literal colors outside the tokens. No control without behavior.

## P09: Publication

**Depends on:** P08. **Areas:** audit, licenses, release.

- [ ] P09.1 Run the full [pre-publication audit](publishing.md#pre-publication-audit).
- [ ] P09.2 Generate `THIRD_PARTY_NOTICES.md`.
- [ ] P09.3 Pin base images by digest.
- [ ] P09.4 Update the README with screenshots and delivered status.
- [ ] P09.5 Clean-clone build and acceptance run of AC-01 to AC-20.
- [ ] P09.6 Tag `v1.0.0` and publish the release.

Exit: every acceptance criterion has dated evidence. The audit is clean.

## After 1.0

Candidates, not commitments, in this order for images:

1. img2img variations of the primary avatar with core nodes.
2. IP-Adapter or a LoRA per character. Both need extra models, so R-MOD-06 and the non-goals must change first.
3. A face detailer.

Other candidates: Character Card V2 import, semantic memory search behind `MemoryRetriever`, group conversations, an
interface in other languages.
