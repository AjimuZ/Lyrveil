# Testing

Status: accepted (2026-11-01). **No test has run yet, because no code exists.** Every test here is planned and assigned to a roadmap phase.

## Levels

| Level | Tool | Runs on | GPU |
| --- | --- | --- | --- |
| Unit | pytest, Hypothesis, Vitest | Any machine | No |
| Integration | pytest with SQLite and fake runtimes | Any machine | No |
| Contract | Schemathesis on OpenAPI, recorded runtime fixtures | Any machine | No |
| End-to-end | Playwright with `compose.test.yaml` and fakes | Any machine with Docker | No |
| Docker | Bash and pytest against the real Compose project | Docker | Partly |
| GPU | pytest with real runtimes and models | Reference machine | Yes |
| Quality | Italian protocol and image grid, with human review | Reference machine | Yes |

## Commands

Planned in `scripts/test.sh`:

```bash
./scripts/test.sh unit
./scripts/test.sh integration
./scripts/test.sh e2e
./scripts/test.sh docker
./scripts/test.sh gpu
./scripts/test.sh quality
./scripts/test.sh all
```

`all` runs everything except `gpu` and `quality`, which need the hardware and are explicit. `gpu` exits with a clear skip message when no GPU is present. A skip is never reported as a pass.

## Layout

```text
backend/tests/{unit,integration,contract}/
orchestrator/tests/
frontend/src/**/*.spec.ts
tests/
  fakes/             fake_kobold, fake_comfy, fake_docker, fake_nvml
  fixtures/          Demo characters, synthetic conversations, recorded runtime responses
  e2e/               Playwright
  docker/
  gpu/
  quality/italian/
```

### Fake runtimes

- `fake_kobold` and `fake_comfy` are HTTP servers that implement exactly the contracts in [Runtime contracts](runtime-contracts.md). They support scripted replies, delays, invalid JSON, out-of-memory errors and crashes.
- `fake_docker` and `fake_nvml` implement `ContainerGate` and `GpuProbe`, including failures and external memory use.
- They live only in `tests/fakes/` and in `compose.test.yaml`, and never enter a production image. A Docker test verifies this.
- The GPU suite records real responses at P02 and P05 into fixtures, so contract tests catch a fake that drifts from reality.

## Catalog

### Character Creator

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-CHR-01 | Create with minimal and full fields | Integration | P03 |
| T-CHR-02 | Age under 18 refused at API, database, import and draft | Unit, integration | P03 |
| T-CHR-03 | Edit creates a version. Saving with no change creates none | Integration | P03 |
| T-CHR-04 | Duplicate copies the card, not conversations or memories | Integration | P03 |
| T-CHR-05 | Export and import round trip preserves the card and asset hashes | Integration | P03 |
| T-CHR-06 | Import refuses a newer format, unknown fields and minors | Unit | P03 |
| T-CHR-07 | Data persists after a process restart | Integration | P03 |
| T-CHR-08 | Version list, diff and restore. Restore creates a new version | Integration | P03 |
| T-CHR-09 | A locked trait survives a draft and a proposal | Unit | P03 |
| T-CHR-10 | Assisted draft: valid output, repaired output, repeated failure | Integration | P03 |
| T-CHR-11 | Upload: valid, wrong bytes, too large. Metadata removed, crop, single primary avatar | Integration | P03 |
| T-CHR-12 | Avatar generation through the shared image job | Integration, GPU | P05 |
| T-CHR-13 | Full creation flow in the browser | E2E | P03 |

### Memory

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-MEM-01 | Extraction stores validated items with sources | Integration | P04 |
| T-MEM-02 | Invalid output changes nothing, keeps the cursor and retries | Integration | P04 |
| T-MEM-03 | Same window twice gives one result | Integration | P04 |
| T-MEM-04 | Deduplication by fingerprint and by similarity | Unit | P04 |
| T-MEM-05 | Retrieval ranking, token budget and maximum items | Integration | P04 |
| T-MEM-06 | Italian stemming and accents match | Unit | P04 |
| T-MEM-07 | FTS syntax in user text is harmless | Unit | P04 |
| T-MEM-08 | Editing a memory creates a revision | Integration | P04 |
| T-MEM-09 | Deletion adds a tombstone. Same source is blocked, a later source is allowed | Integration | P04 |
| T-MEM-10 | Update supersedes, keeps history and references, and the prompt uses the new fact | Integration | P04 |
| T-MEM-11 | Unresolved contradiction stays uncertain, and both are labeled | Integration | P04 |
| T-MEM-12 | Temporary memory expires and does not replace a lasting one | Integration | P04 |
| T-MEM-13 | Explicit user correction wins and is confirmed | Integration | P04 |
| T-MEM-14 | Inference limit: capped confidence, cannot supersede a stated fact, unsourced statements downgraded | Unit | P04 |
| T-MEM-15 | Isolation across conversations. Shared memory only when opted in | Integration | P04 |
| T-MEM-16 | Summaries: ranges, versions, rollups, contiguity, no overwrite | Integration | P04 |
| T-MEM-17 | Summary and extraction jobs are not eligible in image mode | Unit | P04 |
| T-MEM-18 | After restart: memories, cursors and interrupted runs are correct | Integration | P04 |
| T-MEM-19 | Message edit or deletion invalidates derived data, and re-evaluation restores what still holds | Integration | P04 |
| T-MEM-20 | A locked memory gets `needs_review` and does not change | Integration | P04 |
| T-MEM-21 | `ContextBuilder`: budgets at 8192 and 4096, reduction order, mandatory blocks kept, runtime token counts used | Integration | P04 |
| T-MEM-22 | Retrieval evaluation reaches recall at 8 of at least 80 % | Quality | P04 |
| T-MEM-23 | Property: random edit and delete sequences keep state equal to replaying history | Unit | P04 |

### Consistency

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-CON-01 | A fact told to character A never reaches character B's context | Integration | P04 |
| T-CON-02 | Two chats of one character are isolated by default | Integration | P04 |
| T-CON-03 | A state change in one chat leaves another chat unchanged | Integration | P04 |
| T-CON-04 | Canonical data hash is unchanged after 1000 synthetic messages, extraction and summaries | Integration | P04 |
| T-CON-05 | `identity_candidate` is not applied until the user applies it | Integration | P04 |
| T-CON-06 | Deltas are bounded per event, run and day, and always have a reason and sources | Unit | P04 |
| T-CON-07 | Conflicting visual tags are removed | Unit | P05 |
| T-CON-08 | Reply validators V1 to V9 | Unit | P04 |

### Messaging

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-MSG-01 | Create a chat | Integration, E2E | P02 |
| T-MSG-02 | Send is idempotent, and is committed before the job exists | Integration | P02 |
| T-MSG-03 | Streaming: order, persistence every 2 s, final text | Integration, E2E | P02 |
| T-MSG-04 | Regenerate adds variants, and alternatives can be selected | Integration, E2E | P02 |
| T-MSG-05 | Edit | Integration, E2E | P02 |
| T-MSG-06 | Delete | Integration, E2E | P02 |
| T-MSG-07 | Page reload during streaming resumes correctly | E2E | P02 |
| T-MSG-08 | History paging and recovery | Integration, E2E | P02 |
| T-MSG-09 | Simultaneous conversations are served in order, with no mixed text | Integration, E2E | P02 |
| T-MSG-10 | Read state and unread counts | Integration, E2E | P07 |
| T-MSG-11 | Pin, archive, delete, restore, search | Integration, E2E | P02 |
| T-MSG-12 | WebSocket reconnect with replay and resync | Integration, E2E | P02 |
| T-MSG-13 | Crash during streaming leaves `interrupted` | Integration | P02 |

### Images

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-IMG-01 | Prompt composition is deterministic. Safety negative always present | Unit | P05 |
| T-IMG-02 | Scene tag filter | Unit | P05 |
| T-IMG-03 | Request detector on at least 100 phrases | Unit | P05 |
| T-IMG-04 | GPU alternation in the right order, with fakes | Integration | P06 |
| T-IMG-05 | ComfyUI messages map to progress | Unit | P05 |
| T-IMG-06 | Persistence is atomic and idempotent | Integration | P05 |
| T-IMG-07 | The image appears as a message with metadata | Integration, E2E | P05 |
| T-IMG-08 | Images load with ComfyUI off | Integration, E2E | P05 |
| T-IMG-09 | Regeneration adds a variant | Integration | P05 |
| T-IMG-10 | Cancellation at each stage | Integration | P06 |
| T-IMG-11 | Out of memory leads to a tiled retry of the same preset, then one **Rapida** try. The stored preset is the one that produced the file | Integration | P06 |
| T-IMG-12 | Restart in each stage resumes without duplicates | Integration | P06 |
| T-IMG-13 | Contextual mode off by default. A proposal needs a valid event and acceptance | Integration | P05 |
| T-IMG-14 | **Alta** and **Rapida** build the documented graphs: node classes, links, shared seed, final sizes, `ckpt_name` from the profile only, tiled variants. On real hardware, render time and peak VRAM per preset are recorded and the preset rule applied | Unit, real GPU | P05 |

### Models

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-MOD-01 | `config/model-catalog.json` passes validation. Invalid identifiers, moving revisions, unknown templates or presets, and two defaults for one tier are refused | Unit | P02 |
| T-MOD-02 | Each `PromptFormatter` family produces the golden string rendered from the official template for the same turns. No family writes BOS | Unit | P02 |
| T-MOD-03 | A text profile change writes the launch file, stops the runtime, verifies release and loads the new file. A profile above the tier or with a missing file is refused before any mode change | Integration | P06 |
| T-MOD-04 | Custom registration accepts only file names in the two model folders and only `.gguf` and `.safetensors`. Traversal, links out of the folder and other extensions are refused. The hash job stores the profile | Integration | P06 |

### Orchestration

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-ORC-01 | Every legal transition works and every other is refused | Unit | P01 |
| T-ORC-02 | Property: no event sequence runs both runtimes | Unit | P06 |
| T-ORC-03 | Lease rules: one at a time, mode match, expiry, heartbeat | Unit | P02 |
| T-ORC-04 | Release verification with external memory use. No external process is touched | Unit | P06 |
| T-ORC-05 | Low VRAM blocks with a reason and ends the job after the wait | Unit | P06 |
| T-ORC-06 | Each row of the recovery table | Unit | P06 |
| T-ORC-07 | Gate refuses other names and foreign labels. No forbidden call exists | Unit | P01 |
| T-ORC-08 | Fallback profiles | Integration | P06 |
| T-ORC-09 | Drain and shutdown | Integration | P06 |
| T-ORC-10 | Control API refuses a missing or wrong token | Integration | P01 |
| T-ORC-11 | Events replay without duplicates after a backend restart | Integration | P06 |
| T-ORC-12 | Scheduling rules: priorities, batching, fairness, preemption | Unit | P06 |
| T-ORC-13 | A text message during image work is saved, queued, shown and answered | Integration, E2E | P06 |

### Proactive messages

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-SCH-01 | Off by default, globally and per conversation | Integration | P07 |
| T-SCH-02 | Quiet hours, interval, daily caps and unanswered limit, with a fake clock | Unit | P07 |
| T-SCH-03 | Each missed policy. No backdated message | Integration | P07 |
| T-SCH-04 | `send: false` creates no message | Integration | P07 |
| T-SCH-05 | Repetition check discards similar messages | Unit | P07 |
| T-SCH-06 | Never causes a mode change | Unit | P07 |
| T-SCH-07 | Time context values in Italian | Unit | P07 |

### Security

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-SEC-01 | Wrong Host and wrong Origin are refused on REST and WebSocket | Integration | P01 |
| T-SEC-02 | Headers present on every response | E2E | P01 |
| T-SEC-03 | Markup in messages renders as text | E2E | P02 |
| T-SEC-04 | Path traversal attempts on media and asset routes | Integration | P03 |
| T-SEC-05 | Decompression bomb and disguised uploads are refused | Integration | P03 |
| T-SEC-06 | Marker strings never reach logs | Integration | P02 |
| T-SEC-07 | Content guard cases, including minors at each entry point | Unit | P03 |
| T-SEC-08 | Delete refuses paths outside the media root | Unit | P03 |
| T-SEC-09 | Lint rule forbids `innerHTML` and `bypassSecurityTrust` | Unit | P01 |

### Docker

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-DCK-01 | `start.sh` from a clean clone | Docker | P01 |
| T-DCK-02 | `stop.sh`, also when already stopped | Docker | P01 |
| T-DCK-03 | `restart.sh` | Docker | P01 |
| T-DCK-04 | Data survives container recreation and upgrades | Docker | P01 |
| T-DCK-05 | Health checks reach healthy, and report failures | Docker | P01 |
| T-DCK-06 | Scripts run twice with the same result | Docker | P01 |
| T-DCK-07 | Preflight failures print the right advice | Docker | P01 |
| T-DCK-08 | Backend and runtimes have no route to the Internet | Docker | P01 |
| T-DCK-09 | Only `127.0.0.1:4200` is published | Docker | P01 |
| T-DCK-10 | Images contain no data, weights, `.env` or fakes | Docker | P01 |
| T-DCK-11 | Model download resumes, verifies and skips valid files, with a local test server | Docker | P01 |
| T-DCK-12 | Backup and restore round trip | Docker | P03 |

### Real GPU

Executed only on real hardware, with the results recorded in `docs/verification/`.

| ID | Test | Phase |
| --- | --- | --- |
| T-GPU-01 | GPU passthrough in `lyrveil-llm` and `lyrveil-image` | P02, P05 |
| T-GPU-02 | `gemma-4-26b-a4b`, `gemma-4-12b` and `rocinante-x-12b` load. VRAM and RAM measured, tuning rule applied | P02 |
| T-GPU-03 | Tokenization fixture recorded for each template family | P02 |
| T-GPU-04 | Real streaming, token counts, abort | P02 |
| T-GPU-05 | Structured output with grammar on the real model | P03 |
| T-GPU-06 | ComfyUI start, real generation with each image profile, checkpoint load measured | P05 |
| T-GPU-07 | Ten full switches. Container state and NVML samples never show both models | P06 |
| T-GPU-08 | Text sent during real generation is answered after the switch | P06 |
| T-GPU-09 | External VRAM use: blocked with reason, no process touched | P06 |
| T-GPU-10 | Real CUDA out-of-memory handled, by reducing available VRAM on purpose | P06 |
| T-GPU-11 | No orphan process and full VRAM release after `stop.sh` | P06 |
| T-GPU-12 | Everything works with the network disconnected | P06 |
| T-GPU-13 | Two-hour session with 20 images and 3 conversations | P08 |
| T-GPU-14 | [Speed rule](gpu-orchestration.md#measuring-and-tuning) for the default text profile, comparing AutoFit with `MOE_CPU_LAYERS` | P02 |

### Quality

| ID | Test | Phase |
| --- | --- | --- |
| T-QLT-01 | [Italian quality protocol](prompting.md#italian-quality-protocol) | P04, P08 |
| T-QLT-02 | [Image default grid](image-generation.md#default-parameters) for each image profile, with the owner's blind review | P05 |
| T-QLT-03 | Accessibility: `axe` on ten screens, keyboard walkthrough | P08 |
| T-QLT-04 | Responsive checks at 320, 640, 1024 and 1440 px, and 200 % zoom | P08 |

## Acceptance

P09 walks through [AC-01 to AC-20](requirements.md#21-acceptance-criteria) on a clean clone with the real hardware. It records each result in `docs/verification/`.

## Evidence

`docs/verification/YYYY-MM-DD-<topic>.md` holds what ran: the exact command, the versions, the result counts, and what was not run. Use real numbers. A failure stays recorded after it is fixed, together with the fix. Roadmap items link to these files.

## Continuous integration

`.github/workflows/ci.yml`, delivered in P01, runs on every push: lint, type checks, unit, integration, contract, end-to-end with fakes, image builds of the three core services, and `gitleaks`. Hosted runners have no GPU, so CI never claims the GPU suite passed.
