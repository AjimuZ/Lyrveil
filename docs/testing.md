# Testing

Status: accepted (2026-11-01), revised 2026-11-06 for the consistency and image identity tests, the delivered `lint` level and the backend gates, and 2026-11-06 for the `docker` level. **Reconciled on 2026-11-09: the catalog tests named by the exits of P01 to P07 have run, in the phase exit files under [verification](verification/), and the paragraph below is the history of how they came in.** The tests that need the real GPU or a real model (T-QLT-01 and T-QLT-05 to T-QLT-07 on real profiles, T-GPU-10 to T-GPU-13) have not run. The `gpu` level exists (GPU.1), and its T-GPU-01 passed on the reference machine on 2026-11-25 ([evidence](verification/2026-11-25-gpu-level.md)); T-GPU-02 to T-GPU-04 (GPU.2) passed there on 2026-11-25 too ([evidence](verification/2026-11-25-gpu-text-profiles.md)), and so did T-GPU-05 (GPU.4, [evidence](verification/2026-11-25-gpu-structured-output.md)), T-GPU-06 (GPU.5, [evidence](verification/2026-11-25-gpu-image-profiles.md)), T-GPU-07 to T-GPU-09 (GPU.6, [evidence](verification/2026-11-25-gpu-mode-switches.md); T-GPU-07's ten round trips in two runs of five) and T-GPU-14 (P02.7, [evidence](verification/2026-11-25-p02-7-vram-tuning.md)). The `quality` level exists (QLT.1) and automates T-QLT-05, with the SmartCache and SWA padding measurements, and T-QLT-06 ([The `quality` level](#the-quality-level)); only its short smoke mode has run, on `rocinante-x-12b` on 2026-11-26 ([evidence](verification/2026-11-26-quality-level.md)), so neither protocol is passed. The P08 specs `responsive.spec.mjs`, `accessibility.spec.mjs` (T-QLT-03), `performance.spec.mjs` and `empty-loading.spec.mjs` and the unit spec `virtual-window.spec.ts` are written and have not run. The Schemathesis contract test covers only the two health routes, because the other routers are not in the OpenAPI schema ([API](api.md)). The `e2e` level exists (P01.8c): its smoke test passed against the fakes ([evidence](verification/2026-11-06-e2e-level.md)). The fake runtimes of `tests/fakes/` and `compose.test.yaml` are written (P01.8b) ([evidence](verification/2026-11-06-fake-runtimes.md)); their Docker test, `tests/docker/TestFakes.Tests.ps1`, first ran and passed with P01.6a, after two fixes to the test itself. The CI workflow `.github/workflows/ci.yml` is written (P01.8d) and has not run; see [Continuous integration](#continuous-integration). Every catalog test that no exit file records is planned and assigned to a roadmap phase. Delivered so far: the `test` command with its `lint` level, the Pester tests of the scripts in the `unit` level ([evidence](verification/2026-11-06-lint.md)), and the backend's ruff, format and `mypy --strict` gates in `lint` and its pytest unit tests in `unit`, all in the backend test stage ([evidence](verification/2026-11-06-backend-skeleton.md)). The backend part of the `integration` level (database, migrations and health routes) is delivered too ([evidence](verification/2026-11-06-backend-persistence.md)). The orchestrator's gates and unit tests, with T-ORC-01, run in its own test stage ([evidence](verification/2026-11-06-orchestrator-state-machine.md)), and so does T-ORC-07, with the scan that confines the Docker socket to the container gate. The backend's unit tests run the same scan on the backend ([evidence](verification/2026-11-06-container-gate.md)). The orchestrator's integration tests, with T-ORC-10, run in the `integration` level, and its unit tests cover each threshold of the GPU preflight with a fake probe ([evidence](verification/2026-11-06-control-api.md)). T-ORC-03, in `orchestrator/tests/unit/test_leases.py`, and the supervisor's unit tests passed in a targeted `test unit -k` run (P02.2). T-MSG-03, T-MSG-09, T-MSG-12, T-MSG-13 and T-SEC-06 at the integration level, in `backend/tests/integration/test_reply_streaming.py`, and the event hub's tests in `backend/tests/unit/test_event_hub.py` passed in a targeted `test unit` run of those two files (P02.5c); their E2E halves are planned with the chat screens. The integration test of the text lifecycle through the control API, `tests/integration/test_text_lifecycle.py`, is written and has not run yet. The frontend's ESLint, color check and strict build run in `lint`, and its Vitest and Node tests, with T-SEC-09, run in `unit` ([evidence](verification/2026-11-06-frontend-shell.md)). The `docker` level ran in full and passed, with T-DCK-06, T-DCK-07 and T-DCK-13 for `setup.cmd` ([evidence](verification/2026-11-06-setup.md)). T-IMG-04 and T-ORC-02 (P06.1) passed in a targeted `test unit` run of their files on 2026-11-07, with the backend's `gpu.state` tests; the frontend translator spec that checks the Italian GPU labels passed `lint` but was not run on its own then.

## Levels

| Level | Tool | Runs on | GPU |
| --- | --- | --- | --- |
| Lint | Documentation checks, PSScriptAnalyzer, ruff and `mypy --strict` for the backend and the orchestrator, and ESLint, the color check, the generated API types check and the strict `ng build` for the frontend | Any Windows machine with Git, Docker and Node.js 24 | No |
| Unit | pytest, Hypothesis, Vitest, Pester for the scripts | Any machine | No |
| Integration | pytest with SQLite and fake runtimes | Any machine | No |
| Contract | Schemathesis on OpenAPI (the backend's, in `backend/tests/integration/test_contract.py`, run by the `integration` level and the `python` CI job); recorded runtime fixtures are planned | Any machine | No |
| End-to-end | Playwright with `compose.test.yaml` and fakes | Any machine with Docker | No |
| Docker | Pester and pytest against the real Compose project | Docker | Partly |
| GPU | Pester with the real runtimes, models and NVML (`nvidia-smi`) | Reference machine | Yes |
| Quality | Italian, consistency, extraction and image identity protocols, image grid, with human review | Reference machine | Yes |

## Commands

`scripts\lyrveil.cmd test <level>`, delivered in P01.8a. From Git Bash, run `scripts/lyrveil.cmd test <level>`.

```powershell
.\scripts\lyrveil.cmd test lint
.\scripts\lyrveil.cmd test unit
.\scripts\lyrveil.cmd test integration
.\scripts\lyrveil.cmd test e2e
.\scripts\lyrveil.cmd test docker
.\scripts\lyrveil.cmd test gpu
.\scripts\lyrveil.cmd test quality
.\scripts\lyrveil.cmd test quality-rating test-results\quality\italian-full-<time>\sheet.md
.\scripts\lyrveil.cmd test all
```

Python tests run in the test stage of their component's image, `backend/Dockerfile` or `orchestrator/Dockerfile`, so database tests use the same SQLite build as production.
Frontend tests run with Node.js on Windows. Neither bind-mounts source code.

The frontend part of `lint` and `unit`, in [`scripts/lib/FrontendComponent.psm1`](../scripts/lib/FrontendComponent.psm1), runs when `frontend/package.json` is present. It first runs `npm ci --ignore-scripts` in `frontend/` when `node_modules` is missing or older than `package-lock.json`, so the packages always come from the lock. `lint` then runs `npm run lint` and `npm run build`, and `unit` runs `npm run test`: Vitest through `ng test --watch=false`, then `node --test` on `frontend/tools/**/*.test.mjs`. Each script prints its exit status and fails the level when it is not 0.

`unit` passes any arguments after the level to pytest, and then runs only pytest, not the Pester or frontend tests. A path or node ID under `tests/` replaces the default `tests/unit`, so one test runs alone. It runs only in the components whose folder holds every named path, and fails when none does:

```powershell
.\scripts\lyrveil.cmd test unit tests/unit/test_settings.py::test_a_missing_token_is_named
.\scripts\lyrveil.cmd test unit -k logs -x
```

`gpu` takes the names of its test files, without `.Tests.ps1` (letters, digits and hyphens, case as in the file
name), and then runs only those files, in that order, with the same preparation and cleanup. Otherwise its arguments
are trial selectors of the tuning procedure, `<profile>/<trial>` with `*` wildcards, and it runs only
`tests/gpu/Tuning.Tests.ps1` with those trials (see [The `gpu` level](#the-gpu-level)):

```powershell
.\scripts\lyrveil.cmd test gpu StructuredOutput
.\scripts\lyrveil.cmd test gpu ModeSwitch t-gpu-07 rounds-5
.\scripts\lyrveil.cmd test gpu Resilience
.\scripts\lyrveil.cmd test gpu Session short
.\scripts\lyrveil.cmd test gpu rocinante-x-12b/A rocinante-x-12b/batch-*
```

A plain `gpu` run leaves out `tests/gpu/Session.Tests.ps1`, the two-hour session T-GPU-13, which only
`test gpu Session` (two hours) or `test gpu Session short` (150 s) runs.

`quality` takes the names of its test files (`Consistency`, `Extraction`, `ImageGrid`), `short`, the ids of catalog text
profiles and the ids of catalog image profiles, in any order (see [The `quality` level](#the-quality-level)). Without a
profile it runs on every catalog text profile (`Consistency`, `Extraction`) and every catalog image profile
(`ImageGrid`); without a file name it runs all three files:

```powershell
.\scripts\lyrveil.cmd test quality                                   # T-QLT-05, T-QLT-06, T-QLT-02 and T-QLT-07 on every catalog profile
.\scripts\lyrveil.cmd test quality rocinante-x-12b                   # the text files on one profile (and ImageGrid on every image profile)
.\scripts\lyrveil.cmd test quality Extraction rocinante-12b-v1.1     # T-QLT-06 alone on one profile
.\scripts\lyrveil.cmd test quality Consistency short rocinante-x-12b # the smoke mode of T-QLT-05
.\scripts\lyrveil.cmd test quality ImageGrid                         # T-QLT-02 and T-QLT-07 on every image profile
.\scripts\lyrveil.cmd test quality ImageGrid short illustrious-xl-v2.0 # the smoke mode of both on the default image profile
```

`quality-rating` takes the path of the owner's filled blind sheet of T-QLT-01, or of T-QLT-02 and T-QLT-07, and,
optionally, the folder that holds its `key.json` (and `metrics.json`, for T-QLT-01); the sheet's own folder is the
default. It needs no GPU or Docker; see [T-QLT-01](#t-qlt-01-generation-blind-sheet-and-import) and
[T-QLT-02 and T-QLT-07](#t-qlt-02-and-t-qlt-07-image-grid-identity-protocol-and-levers).

Every other level refuses arguments.

`all` runs `lint`, `unit`, `integration`, `e2e` and `docker`. It leaves out `gpu`, `quality` and `quality-rating`, which need the hardware or the owner's sheet and are explicit. `gpu` exits with a clear skip message when no GPU is present. A skip is never reported as a pass.

`integration` builds the test stage of each component that has a `tests/integration` folder, today the backend and the orchestrator, and runs `pytest tests/integration` in it, without network access. The backend's tests use SQLite files in the container's own file system. The test stage has the Python and SQLite build of the runtime image, so WAL and FTS5 are proven on the build that production uses. The tests start the engine, the Alembic command line, the health routes and `main()` against real database files, with Uvicorn replaced by a stand-in. The orchestrator's tests run the control API in process through Starlette's test client, with a fake GPU probe.

Every command exits 0 only when the level ran tests and all of them passed. An unknown level, a missing level and a level with no tests yet exit 1 with a message. `docker` runs the Pester files `tests/docker/*.Tests.ps1` with the host's Docker, and fails when there are none. It builds the images of `compose.yaml` and of `tests/fakes/Dockerfile`, so it needs Docker and takes a while on a first run. See [The `docker` level](#the-docker-level). `gpu` runs the Pester files `tests/gpu/*.Tests.ps1` against the real runtimes; without a GPU it prints `Test level 'gpu' skipped` and `test gpu: SKIPPED (gpu)` and exits 1. See [The `gpu` level](#the-gpu-level). `quality` runs the Pester files `tests/quality/*.Tests.ps1` with the same preparation, skip and cleanup; see [The `quality` level](#the-quality-level).

### The `gpu` level

Delivered with GPU.1 ([evidence](verification/2026-11-25-gpu-level.md)).
[`scripts/lib/GpuLevel.psm1`](../scripts/lib/GpuLevel.psm1) runs it from Windows with Docker and the pinned Pester, in
this order:

1. It fails when `tests/gpu/` holds no `*.Tests.ps1` file. It then reads GPU 0 through NVML with `nvidia-smi`
   (`--query-gpu=index,name,memory.total,memory.used`). When `nvidia-smi` is missing, fails (as it does without an NVIDIA
   driver) or lists no GPU, it prints the reason and a skip message and returns the skip status before any Docker
   call. The command reports `SKIPPED` and exits 1: a skip is never a pass.
2. It refuses to run, touching nothing, while a container named `lyrveil-llm`, `lyrveil-image` (an installation's
   runtime) or `lyrveil-orchestrator` is running. Since GPU.6 the orchestrator counts too, because an installation's
   orchestrator starts its runtime as soon as the card has room.
3. It removes any earlier project `lyrveil-gpu-test` and the volume `lyrveil-gpu-test-data`. It resolves
   `compose.yaml` with [`tests/gpu/compose.gpu-test.yaml`](../tests/gpu/compose.gpu-test.yaml) and the `ai` profile, and
   refuses to go on unless the project is `lyrveil-gpu-test` with only `lyrveil-llm`, `lyrveil-image`,
   `lyrveil-orchestrator` and `lyrveil-backend`, all with `:gpu-test` images, no `container_name` and no port, and only
   tmpfs mounts and read-only mounts of `lyrveil-gpu-test-data` and `lyrveil-models`. Two exceptions: the backend
   mounts the test data volume writable at `/data`, and the orchestrator alone mounts the Docker socket. The
   orchestrator and the backend must read no `env_file`, hold only the variables of the override and carry the level's
   own control token, so the installation's `.env` never reaches them.
4. It builds the two runtime images with the `:gpu-test` tag (the build cache makes later runs short). It creates
   `lyrveil-gpu-test-data` and writes `runtime/llm-launch.env` and `runtime/image-launch.env` into it, from a one-off
   container with no network. The text launch file names the catalog text profile with the smallest file, with
   `CONTEXT_SIZE=4096` and `GPU_LAYERS=99`; the image launch file has the default `RESERVE_VRAM_GB=1.5`.
5. It runs the Pester files of `tests/gpu/`.
6. In a `finally` block, whatever happened after step 3, it runs `down --volumes --remove-orphans` for the project,
   removes `lyrveil-gpu-test-data`, and fails if a container with the project's label or a volume named
   `lyrveil-gpu-test*` is left.

The override keeps the runtimes of `compose.yaml` as they are (GPU request, read-only root, mounts, health checks,
internal network) and mounts the installation's `lyrveil-models` read-only, as `compose.yaml` does. Every command names
the project with `-p`. The level never touches `lyrveil-data`, a running Lyrveil, the `:local` images or the backup
folder. The runtime network publishes no port, so the tests reach the HTTP APIs with `docker compose exec`: `curl` in
`lyrveil-llm`, Python in `lyrveil-image`.

`tests/gpu/Passthrough.Tests.ps1` holds T-GPU-01. It starts `lyrveil-llm`, waits for its health check, and checks the
GPU request (`DeviceRequests` with count -1 and capability `gpu`), that the only volumes are the test data volume and
the models, both read-only, and that a write into `/models/llm` fails. It checks that `GET /api/v1/model` names the
model of the launch file, that the log names the host GPU, and that NVML shows at least half of the model file in VRAM.
It then removes the text runtime, waits up to 60 s for the VRAM to fall back, and starts `lyrveil-image`, so the two
runtimes never run together. For the image runtime it checks the same request, mounts and refused write, that
`GET /system_stats` reports a `cuda` device with the host GPU's name and its memory within 128 MiB of NVML's, and that
NVML shows more than 100 MiB in use for the CUDA context.

`tests/gpu/TextProfiles.Tests.ps1` holds T-GPU-02 to T-GPU-04 (GPU.2,
[evidence](verification/2026-11-25-gpu-text-profiles.md)). It runs after T-GPU-01 and loads the two catalog text
profiles, `rocinante-x-12b` and `rocinante-12b-v1.1`, one at a time. GPU.2 also loaded the three Gemma 4 based
profiles, which left the catalog with [ADR 0045](decisions/0045-drop-gemma-4-rocinante-x-default.md).
For each it rewrites `runtime/llm-launch.env` in the test data volume with the catalog's level-A values (the profile's
context, batch, mmap and KV cache type, `GPU_LAYERS=-1`, `LEVEL=A`), waits for the VRAM of the previous runtime to fall
back, starts `lyrveil-llm` with a 240 s limit, and checks that `/api/v1/model` names the file, that
`/api/extra/true_max_context_length` is the catalog context and that VRAM grew by more than 1 GiB. Every request runs
before the checks, so the numbers are kept even when one fails.

- **T-GPU-02**, for both profiles (`rocinante-12b-v1.1` takes the values GPU.3 decided for `rocinante-x-12b`, so
  this is where its margin at them is checked): NVML before and after the load, the container's memory from
  `docker stats` after the load and after the generations, and the VRAM peak that a background job samples every
  250 ms during 3 generations of 128 tokens (the procedure asks for 20; the run records how many ran). It applies the
  margin rule of [Measuring and tuning](gpu-orchestration.md#measuring-and-tuning) (`margin = total - peak`) and
  fails when the margin is under 512 MiB (A-01). It does not measure the larger context that step 5 would allow, nor
  steps 6 to 9 (batch, speed rule, mmap, KV cache type).
- **T-GPU-03**: tokenizes the family's chat prompt of
  [`backend/tests/fixtures/tokenization/prompts.json`](../backend/tests/fixtures/tokenization/prompts.json) with
  `"special": false`. Each control text of `mistral_v3_tekken` must become its documented identifier as many times as
  it appears (the `gemma4` recording of GPU.2 stays as a fixture, but no catalog profile tokenizes it again); the `chatml` markers must be several tokens each; with `"special": true` the identifiers
  must be the same after a prefix (BOS). The identifiers must equal `<family>.tokens.json`, which the test records when
  the file is missing, with the prompt's SHA-256. `backend/tests/unit/test_tokenization_fixtures.py` checks that every
  fixture prompt, stop list and control list is exactly what the formatter gives, and that each recording names the
  SHA-256 of its family's prompt.
- **T-GPU-04**: streams a 160-token chat reply and checks one closing event, with an empty token, `stop` or `length`,
  at the end; tokens spread over time; `last_token_count` of `/api/extra/perf` within the limit (equal to it for
  `length`) and within 3 tokens plus 10 % of the streamed text counted again; no control text of the family in the
  reply. It then starts a 600-token generation with no stop sequence and the end token banned, sends
  `/api/extra/abort` with its `genkey` after 6 token events, and checks the answer `success` `"true"`, one closing
  event within 10 s, and fewer than 600 tokens generated. That the abort cut the generation short also proves the
  tokens reached the client while the model was still generating.

The results go to `test-results/gpu/text-profiles-<time>.json`, which Git ignores; the passing run's file is copied
to [`docs/verification/2026-11-25-gpu-text-profiles.json`](verification/2026-11-25-gpu-text-profiles.json).

`tests/gpu/Tuning.Tests.ps1` holds T-GPU-14 and steps 1 to 9 of
[Measuring and tuning](gpu-orchestration.md#measuring-and-tuning) for `rocinante-x-12b` (GPU.3,
[evidence](verification/2026-11-25-gpu-tuning.md), which also measured the two Gemma 4 profiles that ADR 0045 later
removed). It only measures.
[`scripts/lib/GpuTuning.psm1`](../scripts/lib/GpuTuning.psm1) plans its trials and computes every decision from them:

- **Trials.** Each trial is the catalog's level A with one lever changed and `LEVEL=CUSTOM`: `A` itself (20 replies
  and 2 long prompts), `context-<next>`, `batch-256`, `batch-1024` and `batch-2048` (the catalog batch is trial `A`),
  `moe-8`, `moe-12` and `moe-16` for a profile with `"moe": true` (20 replies each; no catalog profile since
  ADR 0045), `mmap-0` and `mmap-1` (load, image
  job on `lyrveil-image`, then the measured reload), `kv-q8_0`, and `kv-q8_0-context-<next>`.
- **Measurements.** NVML before and after the load, the ready time, `docker stats` after the load and after the
  workload, and the VRAM peak sampled every 250 ms. A reply is 200 tokens of the family's structured prompt with the
  end token banned. Its speed is the generated tokens over curl's time. A long prompt is a new prompt of at least 6000
  tokens, sized with `/api/extra/tokencount` and answered with one token, so its time is the time to the first token.
  A `moe-*` trial stops early when the speed rule can no longer hold. That happens when more than half of its 20
  replies are under 15 tokens per second, when a reply fails or is short, or when a long prompt fails or takes more
  than 20 s.
- **Decisions.** Step 4 makes level B the default below 512 MiB of margin. Step 5 grows the context when both margins
  are at least 1024 MiB. Step 6 keeps the batch with the largest prompt-speed gain of at least 20 % over 512 that
  keeps 512 MiB. The speed rule (T-GPU-14, for the target default) needs a median of at least 15 tokens per second
  over 20 replies of at least 200 tokens and a first token within 20 s; for a dense default it applies to trial `A`.
  Step 7, for a mixture-of-experts profile only, keeps the fastest setting that meets the margin and speed rules, or
  names the reduced default as the default of every tier when none does. Step 8 keeps `USE_MMAP=1` for
  2 GiB of RAM or 20 % of the reload saved at a speed cost of at most 5 %. Step 9 keeps `f16` until the P04 Italian
  protocol scores `q8_0` ([ADR 0030](decisions/0030-koboldcpp-efficiency-levers.md)) and reports whether `q8_0`
  qualifies on the measurements. A decision whose trial is missing stays undecided and is never guessed.
- **Partial runs.** With selectors, a trial not selected comes from the newest earlier
  `test-results/gpu/tuning-<time>.json` of the same procedure version and GPU, if that record is `measured` or
  `not_loaded` and lists no problem. The version is 2 since ADR 0045 moved the level A of `rocinante-x-12b` to 12288. The results file names the source of every trial. A decision that still lacks a
  trial is skipped in such a run and fails in a full run.

Each run writes `test-results/gpu/tuning-<time>.json`, which Git ignores. It holds the rules, the workload, every trial
with its launch lines, commands and numbers, the reused trials, and the decisions. A full run takes about an hour.

`tests/scripts/GpuTuning.Tests.ps1`, in the `unit` level, tests the plan, the selectors, the launch lines, the early
stop, the trial summary and every decision at and around its thresholds, including records read back from JSON and
the reuse of earlier results files.

`tests/gpu/StructuredOutput.Tests.ps1` holds T-GPU-05 (GPU.4,
[evidence](verification/2026-11-25-gpu-structured-output.md)). It copies none of the backend's prompt, grammar or
rules. It builds the backend's test stage as `lyrveil/backend-test:gpu-test`, its own tag, and runs
[`backend/tests/gpu_probe/structured_task.py`](../backend/tests/gpu_probe/structured_task.py) in a container with no
network, a read-only root and no capability, through `Invoke-GpuBackendProbe`. The probe gives the `character_draft`
job's prompt for a family and a description, with the job's repair prompt when there is a refused output, and the
job's grammar (`schema_to_gbnf(CharacterDraft)`), `max_length`, temperature, attempts and the adapter's total
timeout. It judges each output with the job's own validation, `check_draft` of
`lyrveil/services/character_creator.py`.

The test loads the catalog's default text profile of the target tier (the reference machine's tier since ADR 0027,
`rocinante-x-12b`) at launch level A, as `TextProfiles.Tests.ps1` does. For each of three Italian descriptions of
original adult characters it sends the job's request (`prompt`, `max_context_length`, `max_length`, `genkey`,
`temperature`, `grammar`) to `POST /api/v1/generate`. A refused output is repaired once, with the job's repair prompt.
A request that fails is aborted by its `genkey`. Each draft must meet three conditions:

- every attempt is a JSON object whose top-level keys are the schema's, in order;
- every attempt finishes within the job's 180 s;
- an attempt is accepted within the job's two attempts.

It then generates the first description once without the grammar and reports the grammar's cost:
`1 - median grammar speed / unconstrained speed`, with speed taken as generated tokens per second (`last_eval`). The
cost has no threshold. The request time from curl is recorded too. The results, with every output, go to
`test-results/gpu/structured-output-<time>.json`, which Git ignores.
`backend/tests/unit/test_gpu_structured_probe.py`, in the `unit` level, checks that the probe gives the job's prompt,
repair prompt and grammar, and judges valid, non-JSON, underage and guard-refused drafts as the job does.

`tests/gpu/ImageProfiles.Tests.ps1` holds T-GPU-06 and the real-GPU part of T-IMG-14 (GPU.5,
[evidence](verification/2026-11-25-gpu-image-profiles.md)). Like T-GPU-05 it copies no backend value: in the
backend's test stage, [`backend/tests/gpu_probe/image_presets.py`](../backend/tests/gpu_probe/image_presets.py) gives
the graphs that the `render` stage builds (`build_workflow` with `ImageSettings.for_profile`) for a profile, a preset,
an aspect and seeds. It also computes the preset rule from the measured times. For each catalog image profile, or only
those named after the file name (`scripts\lyrveil.cmd test gpu ImageProfiles noobai-xl-1.1`), the test:

- waits for the VRAM to fall back, starts `lyrveil-image` alone with a 180 s limit, records the ready time and checks
  that `/system_stats` reports a `cuda` device;
- renders the square **Rapida** graph with seeds 101 to 103, then the square **Alta** graph with seeds 201 to 203.
  `Invoke-GpuImageGraph` submits each graph and polls the history every 0.5 s. It records the client's time,
  ComfyUI's own execution time and the size from the saved PNG's header. Each render must end with status `success`
  and one image at the preset's final size;
- samples NVML every 250 ms in the background, separately for each preset, and records the peak, the peak over the
  VRAM before the start and the margin `total - peak`;
- measures the checkpoint load as the first render's time minus the median of the later **Rapida** renders;
- removes the runtime and waits for its VRAM, also when a step fails.

The rule (`rule_document`) takes the nearest-rank 95th percentile of each profile's **Alta** times, leaving out the
first render after the start, which also loads the checkpoint. Each profile gets `alta` when that percentile is at
most 45 s and `rapida` otherwise ([ADR 0026](decisions/0026-two-pass-image-rendering.md)). The default preset is the
verdict of the target tier's default image profile, and `undecided` when that profile did not run. The test fails
when a measured profile gets no verdict. A `rapida` verdict does not fail it: the default then changes as the rule
says. The results go to `test-results/gpu/image-profiles-<time>.json`, which Git ignores.
`backend/tests/unit/test_gpu_image_probe.py`, in the `unit` level, checks the graphs against `build_workflow`, the
percentile, and the rule at 45 s and just above, and without the default profile's times.

`tests/gpu/ModeSwitch.Tests.ps1` holds T-GPU-07 to T-GPU-09 (GPU.6,
[evidence](verification/2026-11-25-gpu-mode-switches.md)). It lets the real orchestrator move the GPU, with no script
stopping or starting a runtime. Its orchestrator is the test-only stage `gpu-test-runtime` of `orchestrator/Dockerfile`
([ADR 0046](decisions/0046-test-only-orchestrator-for-the-gpu-level.md)): the real control API, lifecycle manager,
supervisor and NVML probe, with a gate that acts only on this project's two runtimes. The file builds that image and
the backend's runtime image as `:gpu-test`, writes the level-A launch file of the default text profile
(`rocinante-x-12b`), and creates the two runtime containers without starting them. It reaches the control API and the
backend's REST API from inside their containers (`Invoke-GpuServiceApi`), as the network publishes no port. The items
after the file name select parts: `t-gpu-07`, `t-gpu-08` and `t-gpu-09`, and `rounds-<n>` for fewer round trips. A
part not selected is reported as skipped.

- **T-GPU-09** runs first. The test starts its own container `lyrveil-gpu-test-vram-load` from the image runtime's
  image, with no network and no capability, carrying the project's label. Its PyTorch allocates VRAM until at most
  3072 MiB are free and holds it. Then the orchestrator starts and must:
  - hold the text load in `IDLE` with `blocked_reason` `GPU_INSUFFICIENT_VRAM`, `required_mib` 6000 and a `free_mib`
    within 512 MiB of NVML's free memory;
  - after `PUT /v1/desired-mode image`, hold the image load the same way with 6500;
  - still hold after 12 s, with neither runtime container ever started.
  The load container must keep its PID, start time and zero restarts until the test removes it, by the id its own
  start returned; nothing else is stopped. The text runtime must then load, after the recorded hold event.
- **T-GPU-07** samples NVML every 250 ms and the `State.Running` flag of both runtime containers about every 300 ms in
  background jobs, on one clock, while it runs the round trips (ten unless `rounds-<n>` asks for fewer). Each round
  trip, all through the control API as the backend does it:
  - asks for mode `image` and waits for `IMAGE_READY`;
  - renders the 768 x 768, 8-step graph of `Get-GpuImageWorkflow` with the default image checkpoint under an image
    lease. A fresh ComfyUI loads the checkpoint;
  - asks for mode `llm`, waits for `LLM_READY` and generates 16 tokens under a text lease.
  The checks:
  - no container sample shows both runtimes running;
  - NVML shows exactly one model phase per expected runtime, in order: text, then image and text for each round.
    `Test-GpuExclusiveTimeline` of [`scripts/lib/GpuSwitches.psm1`](../scripts/lib/GpuSwitches.psm1) uses
    hysteresis: a phase starts at 4096 MiB above the baseline and ends only at a sample within 1024 MiB of it, so two
    models resident together would merge into one phase. Each phase must hold container samples of its own runtime
    alone;
  - the two container ids are the same before and after, so the switches only stopped and started them;
  - every switch is timed from the orchestrator's events (`Get-GpuSwitchTime`): the stop with its verified release,
    then the load of the other runtime. The planning target of 150 s for a full switch is recorded, not asserted.
- **T-GPU-08** starts the backend on the test data volume, owned by 10001 as `setup.cmd` leaves a volume, beside the
  test orchestrator. It loads the demo characters, opens a conversation and asks for a **Rapida** square image. Once
  the orchestrator reports `GENERATING_IMAGE` under the image job's lease, it sends a text message with intent `text`.
  The checks:
  - the send answers 202 with the reply `queued`;
  - the reply's `created_at` falls between the image lease's grant and its release in the orchestrator's events;
  - the image completes;
  - the reply completes with text, its job completes, and the events run in this order: image lease released, image
    runtime stopped, text runtime ready, then the reply's first text lease.

Since a full round trip takes about 54 s on the reference machine, ten of them and the setup take about 11 minutes.
The results go to `test-results/gpu/mode-switches-<time>.json`, which Git ignores. The file also holds, for a state
that is not reached, the last log lines of that runtime. `tests/scripts/GpuSwitches.Tests.ps1`, in the `unit` level,
covers the phases at their limits, the check with both runtimes running, with two models resident together and with
no runtime, and the switch times. `orchestrator/tests/unit/test_gpu_test_gate.py` checks that the test gate sends only
the documented calls to the test project's containers and refuses an installation's.

`tests/gpu/Resilience.Tests.ps1` holds T-GPU-10 to T-GPU-12 (GPU.7,
[evidence](verification/2026-11-26-gpu-resilience.md)). It prepares the same stack as `ModeSwitch.Tests.ps1`: the
test-only orchestrator, the backend, the level-A launch file of `rocinante-x-12b` and the two runtime containers. The
items after the file name select parts: `t-gpu-12` and `t-gpu-11` run in that order, because T-GPU-11 stops the stack;
without items both run. `t-gpu-10` runs only alone (`scripts\lyrveil.cmd test gpu Resilience t-gpu-10`).

- **T-GPU-12** starts the text runtime and the backend and records, for the backend, the orchestrator and both
  runtimes, the networks Docker reports (each must be internal) and the published ports (none). In each running
  container a probe reads the routing tables and tries one TCP connection to `1.1.1.1:443` and one lookup of
  `example.com`: no default route, no connection and no resolved name are allowed. The probe uses Python, or the shell
  with `curl` and `getent` in `lyrveil-llm`, which has no Python; the image runtime is probed while it runs for an
  image job. A one-off container on Docker's default bridge is the control and must show a default route. The stack
  must then answer a text through the backend, render a **Rapida** square image and answer again after the switch
  back. **What still needs a manual unplug:** the test covers the backend, the orchestrator and both runtimes on the
  real GPU with no route out. It does not cover the frontend and the browser (the frontend joins the edge network to
  publish `127.0.0.1:4200` and is not part of the test project), nor Windows, WSL2, Docker Desktop and `start.cmd`
  with the network adapter disconnected. For AC-18 that remains one manual check on the reference machine: disconnect
  the network, run `start.cmd`, open the app, send a message and ask for an image.
- **T-GPU-11** runs `Invoke-LyrveilStop`, the code of `stop.cmd`, on the test project, with an env file that does
  not exist (so Compose reads `.env.example`, never the installation's `.env`) and a backup folder in Pester's test
  drive. The test data volume has no installation label, so the backup copy is refused with a warning and that folder
  is never created; any other warning fails. Before the stop it lists the processes of every running container with
  `docker top`, and a one-off container in the Docker VM's PID namespace (`--pid host`, no network, no capability,
  user 10001, reading command lines only) must see each container's processes and the KoboldCpp program. After the
  stop the same probe must see none of them and no KoboldCpp or ComfyUI process. The checks:
  - the stop exits 0, and the orchestrator's log holds the drain, `UNLOADING_LLM` with reason `drain` then `IDLE` with
    `release verified`;
  - every container that ran is `exited` with an exit code, none was killed for memory, and none starts again in the
    next 10 s;
  - NVML's memory in use falls within 60 s to less than the run's baseline plus 128 MiB.
- **T-GPU-10** forces a load too large for the memory left free: its own VRAM load (the container of T-GPU-09) leaves
  at most 6912 MiB free, above the orchestrator's 6000, and the launch file is level A with `GPU_LAYERS=99`,
  `CONTEXT_SIZE=24576` and `BATCH_SIZE=2048`, with `runtime/` owned by 10001 so the backend can lower it. It expects
  `LLM_LOAD_FAILED` with `oom`, the launch file at level B, a level-B load that answers, and its VRAM load untouched.
  **Pending GPU.7b:** on this machine the NVIDIA driver on Windows falls back to shared system memory, so no
  allocation fails (CUDA reported 11090 MiB free while the load held 4352 MiB, and the load placed 11924 MiB of
  buffers on CUDA0). In every run that does not select `t-gpu-10` its five cases are reported as skipped with that
  reason, never as passed.

The results go to `test-results/gpu/resilience-<time>.json`, which Git ignores. `tests/scripts/GpuResilience.Tests.ps1`,
in the `unit` level, covers the readers of `docker top`, the process probe, the route probe and the orchestrator's log,
and the judges of [`scripts/lib/GpuResilience.psm1`](../scripts/lib/GpuResilience.psm1): an orphan, a reused PID, a
service the probe never saw, a container that started again, a network that is not internal, a published port, a
default route and an incomplete probe. They are written and have not run yet.

The Pester cases "The gpu level" in `tests/scripts/TestCommand.Tests.ps1`, in the `unit` level, check the wiring with
stand-in `docker.cmd` and `nvidia-smi.cmd` first on `PATH` and a fixture test file: the skip without a GPU, with no
Docker call and no pass; the order (removal, build, data volume, launch files, tests, removal); the removal after a
failing test, a failed build and a refused configuration; the refusal while an installation's runtime runs; and the
configuration check itself. Since GPU.2 they also check the ASCII request JSON, the `docker stats` amounts and the
level-A launch lines. Since GPU.3 they check that a malformed trial selector, or one the catalog's plan cannot
serve, exits 1 before any GPU or Docker call. Since GPU.4 they check that a test file named as an argument runs alone,
with the same cleanup. Since GPU.5 they check that identifiers after a file name reach that file as its `Select`
parameter. Since GPU.6 their configuration holds the orchestrator and the backend, and they check the Docker socket
outside the orchestrator, a writable models mount in the backend, another host mount, a variable from `.env`, another
token and a missing service. They are written and have not run yet. Since GPU.8 they also check that a plain `test gpu`
leaves out `tests/gpu/Session.Tests.ps1`, and that `test gpu Session` and `test gpu Session short` run it with the
same cleanup.

`tests/gpu/Session.Tests.ps1` holds **T-GPU-13** (GPU.8), the two-hour session, and is **opt-in**: a plain
`scripts\lyrveil.cmd test gpu` leaves it out (`$script:OptInGpuTestFile` of
[`scripts/lib/TestCommand.psm1`](../scripts/lib/TestCommand.psm1)), because it runs for hours. Two options run it, both
with the usual preparation and cleanup:

```powershell
.\scripts\lyrveil.cmd test gpu Session          # the full session: 7200 s, 20 images, 3 conversations
.\scripts\lyrveil.cmd test gpu Session short    # the same driver: 150 s, 2 images, 3 conversations
```

It prepares the stack like `Resilience.Tests.ps1`, waits for the text runtime, then drives the session by itself
through the backend's REST API and the orchestrator, with no person involved. The plan, the schedule, the driver and the
verdict are in [`scripts/lib/GpuSession.psm1`](../scripts/lib/GpuSession.psm1):

- **Events.** A text message falls due every 90 s (every 30 s in the short mode), taking the three conversations in turn,
  80 texts in all. The 20 images (Rapida, square) are spread evenly, the first at 180 s and the last at 7020 s, again in
  turn. The events run one after the other, each when it falls due or at once after a slower one; an image makes the
  orchestrator switch to the image runtime and back, and that switch shows in the latency of the text that follows.
- **Recorded.** Every 30 s (15 s in the short mode), after every event and at the end, a sample holds the used VRAM of
  GPU 0 through NVML, the host's free RAM (`Win32_OperatingSystem`), the memory `docker stats` reports for each
  container of the test project and the orchestrator's state. A background job also reads NVML every 250 ms for the
  peak. Each event holds its latency as the driver measured it, its result and its error text.
- **Verdict.** It passes only when the session was not stopped early (five failed events in a row stop it, so a broken
  stack does not burn two hours); it lasted its whole duration; every planned event ran and succeeded; all 20 images
  completed and each of the three conversations had a successful text; at least half the expected samples were taken
  and NVML could be read in each; the host never had less than 1024 MiB of RAM free; and the lowest VRAM of the last
  quarter of the planned duration is at most 1024 MiB above that of the second quarter, so a model or cache that is
  never released shows. Latency (p50, p95 and maximum for texts and images) is recorded and summarized but has no
  limit of its own: a reply over its 300 s timeout, or an image over 420 s, is a failed event.

The results, with every sample, every event, the verdict and its summary, go to
`test-results/gpu/session-<mode>-<time>.json`, which Git ignores. The file is written when the test ends, also when it
fails, so an interrupted run leaves no record. `tests/scripts/GpuSession.Tests.ps1`, in the `unit` level, covers the
shortened mode of the same driver on a virtual clock with stand-in actions: the plan and schedule, the order and
non-overlap of the events, a late event, a failed event, the early stop, an unreadable sample, a rising VRAM floor, low
RAM and a short session. They are written and have not run yet.

**Result so far.** The short mode ran once on the reference machine (RTX 4070, 12 GB) on 2026-11-26 through
`scripts\lyrveil.cmd test gpu Session short`: 4 Pester cases passed, nothing was left behind, and the verdict passed.
The session lasted 196.3 s (the events overran the planned 150 s), with 7 events (5 texts, 2 images) and 0 errors; text
latency p50 6.9 s and maximum 38.2 s (the maximum follows an image switch), image latency p50 31.2 s and maximum
35.8 s; VRAM peak 9823 MiB in the samples (10227 MiB in the 250 ms job); the host's free RAM never fell below
1268 MiB, close to the 1024 MiB limit. **The two-hour run (`test gpu Session`) has not been made**, so T-GPU-13 is not
passed; it needs two hours of the GPU, and its host RAM limit may be the first check to fail on this machine.

### The `quality` level

Delivered with QLT.1 for the automated parts of P04's quality exit: T-QLT-05, the
[consistency protocol](consistency-engine.md#consistency-protocol) with the SmartCache and SWA padding measurements of
[ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md), and T-QLT-06, the
[extraction evaluation](consistency-engine.md#extraction-evaluation). Delivered with QLT.2: the generation, the
automatic metrics and the blind sheet of T-QLT-01, with the `quality-rating` level that imports the owner's ratings
(see [T-QLT-01](#t-qlt-01-generation-blind-sheet-and-import)). Delivered with QLT.3: the renders, the blind sheet and
the import of T-QLT-02 and T-QLT-07
(see [T-QLT-02 and T-QLT-07](#t-qlt-02-and-t-qlt-07-image-grid-identity-protocol-and-levers)). C7, the owner's blind test
of 20 replies, is not part of it.

`Invoke-QualityTestLevel` of [`scripts/lib/TestCommand.psm1`](../scripts/lib/TestCommand.psm1) runs
`tests/quality/*.Tests.ps1` through `Invoke-GpuLevel` of [`scripts/lib/GpuLevel.psm1`](../scripts/lib/GpuLevel.psm1),
so it has the gpu level's steps exactly: the skip without a GPU (`test quality: SKIPPED (quality)`, exit 1), the
refusal while an installation's runtime or orchestrator runs, the project `lyrveil-gpu-test` with its own data volume
`lyrveil-gpu-test-data`, `lyrveil-models` read-only, the configuration check and the removal of everything at the end,
also on failure. `Resolve-QualityArgument` of [`scripts/lib/QualityLevel.psm1`](../scripts/lib/QualityLevel.psm1) sorts
the arguments before any GPU or Docker call: a capitalized name must be a test file of `tests/quality`, and `short`, the
catalog text profile ids and the catalog image profile ids reach every file that runs as its `Select` parameter (a file
ignores the profiles of the kind it does not run). Anything else exits 1 with
`Nothing ran.`. Both files hand `runtime/` of the data volume to the backend's user 10001, as an installation's volume
has it, and write their launch lines as that user (`Set-GpuTestLaunchLine -AsBackendUser`), so they run in either order.

`tests/quality/Consistency.Tests.ps1` holds **T-QLT-05**. It prepares the stack like `tests/gpu/Session.Tests.ps1` (the
test-only orchestrator, the backend, both runtime containers), turns diagnostic mode on so that every reply keeps its
prompt, and loads the demo characters, whose band and narration must match the script. For each selected profile:

- **Profile.** It makes the profile active through the backend, as **Applica ora** does: `PATCH /settings/models`, then
  `POST /settings/models/apply`, the reload job, `LLM_READY`, and `/api/v1/model` naming the profile's file.
- **Conversations.** One conversation per demo character follows
  [`tests/quality/consistency/script.json`](../tests/quality/consistency/script.json): 108 shared user messages with
  three time jumps, two corrections and a 26-message topic change, and each character's 12 identity probes in blocks of
  four ending at messages 10, 60 and 120 (name, age, occupation, place, gender, main trait and a fact of block 2). The
  driver waits for each reply (300 s at most), counts its tokens with the loaded model (`/api/extra/tokencount`), and
  judges a probe with its keyword lists (`Test-QualityProbeAnswer`: one expected fragment and no rejected one, compared
  lowercase without accents). A conversation stops at the first reply that does not complete, and the missing replies
  count against it.
- **C6.** One more conversation with the script's change character (Nerea) runs its first 60 messages on the next
  catalog text profile (wrapping), then the backend changes back to the measured profile for messages 61 to 120.
- **Checks.** The `checks_json` of every reply's active variant is read from `/data/db/lyrveil.sqlite3`, opened
  read-only, by the backend's own Python (`docker compose exec`).
- **Scores.** `Get-QualityConsistencyScore` computes C1 (at least 95 % of the probes right, and no delivered reply with
  a V6 or V10 hit), C2 (at most 1 reply with a delivered V11 hit per 200), C3 (V13, at most 2 per 100), C4 (per band, a
  median within the target of [Length bands](prompting.md#length-bands) and at most 10 % of the replies cut by N4), C5
  (narration spans delivered for `nessuna`, with N3's removed spans as the raw tendency) and C6 (C1 and C4 on the change
  conversation after the change). C1, C2, C4, C6 and the completion of every planned reply are hard and fail the test;
  C3 and C5 are recorded; C7 is recorded as `not_run`.

Then it removes the stack and measures **SmartCache** for each profile with `lyrveil-llm` alone, at the profile's level A
with `SMART_CACHE` 0 and then 2 and `LEVEL=CUSTOM`. Each trial replays the stored prompts of the last 20 replies of the
first conversation in pairs: the first prompt, then the extraction prompt of a T-QLT-06 window under its grammar (the
structured task), then the second prompt, whose `last_process` from `/api/extra/perf` is the measurement. It is read in
KoboldCpp's own unit, which behaves as milliseconds per prompt token (like `last_eval` in T-GPU-04); both trials replay
the same prompts, so the ratio of the processing times is kept. `docker stats` of the runtime is read after every
request for the peak RAM, and `docker info` gives the memory Docker sees.
`Get-QualitySmartCacheDecision` gives 2 when the median falls by at least 50 % and the peak stays at least 4096 MiB below
that memory, 0 when either fails, and `undecided` when a trial is missing, measured nothing or failed. The record also
says whether the catalog may carry 2: its validator allows `smart_cache` 2 only for a profile whose `min_tier` is
`target`, which neither catalog profile has. **SWA padding** is planned only for a `gemma4` profile
(`Get-QualitySwaPaddingPlan`), so for both catalog profiles it is recorded as `not_applicable` with that reason
([ADR 0045](decisions/0045-drop-gemma-4-rocinante-x-default.md)). `Get-QualitySwaPaddingDecision` holds the rule for a
`gemma4` profile (the smallest value within 10 % of the best median `last_process` that keeps 512 MiB of margin and
the speed rule); the file has no runner for those trials, and would fail rather than guess for such a profile.

`tests/quality/Extraction.Tests.ps1` holds **T-QLT-06**. Like T-GPU-05 it copies no backend prompt, grammar or rule:
[`backend/tests/gpu_probe/extraction_eval.py`](../backend/tests/gpu_probe/extraction_eval.py), in the backend's test
stage with no network, gives the `memory_extraction` job's prompt (`extraction.render_prompt`) for each of the 40
windows of [`backend/tests/fixtures/extraction_eval/windows.json`](../backend/tests/fixtures/extraction_eval/windows.json),
with the job's grammar, length and temperature, and the repair prompt of a refused output. For each profile the test
starts `lyrveil-llm` alone at level A, generates every window, has the probe judge the outputs with the job's
`validate_result`, repairs each refused one once, and has the probe score the results. The probe drops an item as the
job does (a source outside the window, an unknown or missing target, blank text) and stores provenance as guard 1
does (`checked_provenance`). The four targets:

- at least 85 % of the 43 expected items found with one of their kinds, their provenance and every keyword group;
- at most 3 % of the 14 inferences (what a window only implies) stored as `stated`;
- no stored item that matches one of the 4 expected `identity_candidate` items under another kind, which would change
  canonical data;
- at least 98 % of the windows valid after the repair, so all 40.

The short mode (`short`) of both files is a smoke run of the same code that fits in about ten minutes: T-QLT-05 runs the
first probe block alone (4 messages per conversation, the change after message 2, one SmartCache pair) and T-QLT-06 the
first 5 windows. Its results file says it is no evidence for the catalog.

**Result so far** ([evidence](verification/2026-11-26-quality-level.md)). Only the short mode has run, on
`rocinante-x-12b`, on 2026-11-26, and every run left nothing behind. `test quality Consistency short rocinante-x-12b`
passed once (6 Pester cases, 291.8 s):

- 16 of 16 replies completed, 12 of 12 probes were right, and no V6, V10, V11 or V13 hit was delivered;
- the medians were 13, 63.5 and 9 tokens for `brevi`, `lunghi` and `brevissimi`, with no reply cut, and C6 held after
  the change back from `rocinante-12b-v1.1`;
- SmartCache kept 0 (`last_process` fell 1.76 % on one pair with a three-message history).

A second run, with the final code, missed C1 at 11 of 12. Tessa answered "Apprendo pozione!" to "Che lavoro fai?", and
that probe's list lacked the `pozion` of her later occupation probe. Same-topic probes now share their fragments, and a
unit case checks it.

`test quality Extraction short rocinante-x-12b` **failed** its found-rate target. All 5 outputs were valid, but 0 of
the 5 expected items were found: the model returned no memory for four windows and, for one, a `duplicate` of a memory
the window does not show, which the job drops. **The full protocols have not run on either profile**, so T-QLT-05 and
T-QLT-06 are not passed, and the extraction result is a finding for the P04 tuning round.

#### T-QLT-01: generation, blind sheet and import

The [Italian quality protocol](prompting.md#italian-quality-protocol) asks for three demo characters, scripted
conversations of at least 120 messages with time jumps, corrections and a long topic change, automatic metrics, and the
owner's rating. Those conversations are the ones of T-QLT-05 (`tests/quality/consistency/script.json`), so
`Consistency.Tests.ps1` is also the **generation** of T-QLT-01 and runs nothing twice: `test quality` and `test quality
Consistency` (on the profiles named, both catalog profiles by default) leave
`test-results/quality/italian-<mode>-<time>/` beside the consistency results. `tests/quality/italian/`, once planned
for the protocol's own scripts, is not needed. The script gives each character its `question_frequency`, which must
match the demo card as the band and narration do.

[`scripts/lib/ItalianQuality.psm1`](../scripts/lib/ItalianQuality.psm1) computes everything from the recorded replies of
the main conversations (the change conversation of C6 starts on another profile):

- **M1**, the share of replies without a V8 hit. V8 is the backend's stop-word check, and it reads only replies of at
  least 8 words, so a shorter reply counts as Italian. At least 98 %.
- **M2**, the V9 hits (the calque and artifact list, with its opening and repetition rules) per 100 replies. At most 1.
- **M3**, from the texts: the share of replies whose first three words equal those of one of the 10 previous replies of
  the same conversation. At most 5 %.
- **M4**, per character, the share of replies with a question mark against an expected value from the card's
  `question_frequency` (`raramente` 0.10, `a_volte` 0.30, `spesso` 0.50; the protocol does not give numbers, so these
  are this module's reading). Every character must be within 20 points.

`sheet.md` is the owner's file. It mixes, in one random order per part, the replies sampled for every selected profile:
**Part 1** holds 30 replies per profile to rate from 1 to 5 for naturalness (M5), each with the character and the user
message; **Part 2** holds 20 replies per profile (the same number per character, no reply twice) whose author the
owner names among the three characters (M6), with the reply alone. Items have opaque ids (`N01`, `A01`), so the sheet
holds no profile name; `key.json` beside it records the profile and character of each id and is not to be opened
before the rating. A run that has fewer replies than the samples ask for (the short mode) is marked `smoke`, and
identity probe replies, which answer a scripted question, are used only to fill a sample that nothing else can. M6 takes
at most half of a character's replies so that M5 keeps some. `metrics.json` holds M1 to M4 per profile.

`scripts\lyrveil.cmd test quality-rating <sheet.md>` reads the filled sheet (`Rating: 4`, `Author: Nerea`) with
`Read-ItalianRating`, checks it against the key (the sheet's id, unknown or repeated ids, a rating outside 1 to 5, an
author who is no character) and applies the targets with `Get-ItalianVerdict`, per profile: M5, the average rating, at
least 4; M6, the share attributed to the right character, at least 80 %; and M1 to M4 from `metrics.json`. A profile is
`met`, `missed`, or `incomplete` when a value is blank or a metric is missing: nothing is guessed. It prints the verdict,
writes `verdict.json` beside the key, and exits 0 only when the sheet read without a problem and every profile is `met`.
A verdict from a `smoke` sheet says it is no evidence.

**Result so far.** Only the generation of the short mode has run on the real stack: `test quality Consistency short
rocinante-x-12b` on 2026-11-26 wrote the sheet, the key and `metrics.json` and left nothing behind
([evidence](verification/2026-11-26-quality-level.md)). No owner has rated a sheet, and no full run has been made, so
**T-QLT-01 has no verdict** and is not passed. Importing the unfilled real sheet exited 1 and reported all its items
blank, as it must.

The results go to `test-results/quality/consistency-<mode>-<time>.json` and `test-results/quality/extraction-<mode>-<time>.json`,
which Git ignores: every reply with its text, probe verdict, length and checks, every score against its target, every
trial and decision, and every extraction output with its verdict.
`tests/scripts/QualityLevel.Tests.ps1`, in the `unit` level, covers the arguments, the plan built from the real script,
the probe and narration readers, every score at and just past its target, the trial launch lines and the SmartCache and
SWA padding rules. `tests/scripts/ItalianQuality.Tests.ps1` covers M1 to M4 at and past their targets, the blind sheet
(sample sizes, no profile name, mixing, a fixed seed, probe fill), the import of a filled sheet and the verdict, on
synthetic replies and ratings. `backend/tests/unit/test_gpu_extraction_probe.py` checks that the probe gives the job's prompt and
repair prompt, refuses what the job refuses, applies guard 1, drops what the job drops, scores each target at its
threshold, and that the fixture holds 40 valid windows. The cases "The gpu level" of `tests/scripts/TestCommand.Tests.ps1`
also cover `test quality`: both files with their items and the cleanup, one named file, a refused argument before any
GPU or Docker call, and the skip without a GPU; and `test quality-rating`, with its usage and an import that passes and
one that fails on a blank sheet.

#### T-QLT-02 and T-QLT-07: image grid, identity protocol and levers

Delivered with QLT.3 ([evidence](verification/2026-11-26-image-quality.md)). `tests/quality/ImageGrid.Tests.ps1` renders
the [image default grid](image-generation.md#grid) for each catalog image profile and, on the default image profile
of the target tier, the [image identity protocol](image-generation.md#image-identity-protocol) with the
[measured levers](image-generation.md#measured-levers). Like T-GPU-06 it copies no prompt, size or sampler value: it
builds the backend's test stage and runs `backend/tests/gpu_probe/image_quality.py plan`, which gives the graph of every
cell with the backend's own `VisualConsistencyService.build` and `build_workflow`. Then, profile by profile, it starts
`lyrveil-image` alone, renders every cell, saves its image as a JPEG outside the container
(`Invoke-GpuImageGraph -SavePath`), and removes the runtime and waits for the VRAM.

- **Grid (every image profile).** 3 characters (the demo ones), 2 scenes and 3 seeds, **Alta**, portrait. Each image is
  rendered at the default settings and with one setting changed: CLIP skip 1, CFG 4.5 and 7, second-pass `denoise` 0.35
  and 0.55. That is 6 renders for each of the 18 combinations, 108 for a profile. One setting at a time keeps the grid at
  108 renders; the full product of the documented values would be 324.
- **Sizes (default profile).** The same 18 combinations at landscape, tall and wide at the default settings (54 more).
- **Identity protocol (default profile).** The 3 demo characters and the synthetic Ottavia (52) and Bruno (70), 4 scenes
  and 3 seeds at the default settings, with the size `auto` gives for the scene's framing: 60 renders. The same 60 with
  weight 1.1 on the `hair_color` and `eye_color` tags (`(red hair:1.1)`), inserted in the probe because adoption is what
  the measurement decides.
- **PAG (default profile).** The protocol's renders at the first seed (20) at `PerturbedAttentionGuidance` `scale` 1.5
  and 3, a node inserted between the checkpoint and the first pass's `KSampler`. Scale 0 is the protocol's baseline
  render of the same character, scene and seed.
- **Not done.** The protocol asks for scenes "written by every catalog text profile". The scenes here are four fixed
  `ImageScene` values with vocabulary tags (cafe, street, park, library), so the second target of T-QLT-07, the gap
  between a text profile and the default, is recorded as `not_run`.

`short` plans one character, one scene and one seed: 9 grid renders, 2 identity renders and 2 PAG renders on the default
profile (13), 6 on another profile. It is a smoke run and its verdict says so.

**The blind review.** `ImageQuality.psm1` turns the renders into `test-results/quality/image-<mode>-<time>/`:
`sheet.md` for the owner, `images/` with every image under an opaque name, `key.json` and `renders.json`. The sheet names
no profile, setting or lever and has four parts, each in a random order:

| Part | Item | The owner answers |
| --- | --- | --- |
| G | One grid image | `Defect: yes` or `no` (a visible defect: limbs, hands, face, objects, smears, stray text) |
| I | One identity image and the character's expected attributes | `Hair color:`, `Hair length:`, `Eyes:`, `Age band:`, `Outfit:`, each `ok` or `wrong` |
| P | A PAG image and the scale-0 image of the same render, the PAG one on a random side A or B | `Better: A`, `B` or `same` |
| W | A weighted image and its baseline, on a random side | `Style change: yes` or `no` |

`scripts\lyrveil.cmd test quality-rating <sheet.md>` recognizes this sheet by its first heading, reads `key.json` from
its folder, writes `verdict.json` and exits 0 only when no answer is missing or invalid and the verdict is `met`.
Nothing is guessed: a blank or invalid answer is listed and leaves its decision `incomplete`. The decisions, which are
code in `Get-ImageVerdict` and are tested with synthetic answers:

| Decision | Rule |
| --- | --- |
| Default image profile (T-QLT-02) | Each profile's share of default-setting images with a defect. The default changes (`change`, naming the candidate) only when another profile's share is strictly lower; a tie keeps it. A change needs an ADR, so the verdict is a proposal. Fewer than two profiles reviewed: `undecided` |
| CLIP skip, CFG, `denoise` (T-QLT-02) | The default setting stays unless another value has a defect share at least 10 points lower (this module's reading of "unless the grid shows a visible defect": the document gives no number); the lowest such value wins |
| Tall and wide (T-QLT-07) | Tall is kept when its defect share is at most portrait's, wide when it is at most landscape's, else removed |
| Attribute accuracy (T-QLT-07) | Correct checks over all checks of the baseline identity images, at least 0.90 |
| Identity weight 1.1 (T-QLT-07) | `adopt` when the weighted images' accuracy is at least 5 points higher and no pair shows a style change |
| PAG scale (T-QLT-07) | A scale is adoptable when its image wins at least 60 % of the pairs (a `same` is no win) and the nearest-rank 95th percentile of its render seconds is at most 45 s; the one with more wins is adopted, the smaller on a tie, else scale 0 |

The render seconds of the PAG cells are the client-side seconds of an **Alta** render with the checkpoint already loaded
(the grid renders first, so the first render after the start, which loads it, is never a PAG cell). The verdict's `status` is `met` when every decision is made and the accuracy
target holds, `missed` when the accuracy is below target, and `incomplete` when any answer is missing, also when the sheet
has no identity image because the default image profile was not part of the run.

**Result so far.** Only the short mode has run, on the default profile: 13 renders on the RTX 4070 and the unfilled sheet
imported with exit 1. **No full run has been made and no owner has reviewed a sheet, so T-QLT-02 and T-QLT-07 have no
verdict** and are not passed. `tests/scripts/ImageQuality.Tests.ps1` covers the sheet (blind, one item per render, random
sides, fixed seed), the reader, and every decision at and around its threshold on synthetic renders and answers;
`backend/tests/unit/test_gpu_image_quality_probe.py` covers the plan (the counts, the sizes, the PAG node, the weight).

### The `docker` level

Delivered with P01.8b, P01.6a, P01.6b, P01.6c, P01.6d and P01.7b ([setup evidence](verification/2026-11-06-setup.md),
[lifecycle evidence](verification/2026-11-06-lifecycle.md), [models evidence](verification/2026-11-07-models.md),
[data files evidence](verification/2026-11-07-data-files.md)). It runs with the pinned Pester only;
pytest joins it when a Docker test needs Python. The files:

- `tests/docker/TestFakes.Tests.ps1` checks that no production image holds `tests/fakes` and that `compose.test.yaml`
  touches nothing of an installation ([Fake runtimes](#fake-runtimes)).
- `tests/docker/Setup.Tests.ps1` holds T-DCK-06, T-DCK-07 and T-DCK-13. It judges each failing preflight row from
  stand-in facts, and checks that setup then prints the advice and `exit status 2` and creates no `.env`. With a
  stand-in `docker.cmd` that always fails, it runs the real probe, and it runs `setup.cmd` with an unknown and a planned
  option through `cmd.exe`. It runs setup twice against the project `lyrveil-setup-test` and compares `.env`, the
  volume labels and owners and the runtime container. It checks the `.env` ACL and that both volumes survive
  `docker compose down -v`, and runs PSScriptAnalyzer. It also checks that every root `.cmd` file holds only a comment,
  the call to `scripts\lyrveil.ps1` with its own command and `%*`, and the exit.
- `tests/docker/Lifecycle.Tests.ps1` holds T-DCK-01 to T-DCK-05. Against the project `lyrveil-lifecycle-test`, it runs
  start from nothing (no `.env`, volumes or containers), so start runs setup, then checks the ready line, the health of
  the backend and the frontend, and that the empty app answers on its port. It runs status and `status -Json`, logs,
  stop twice, restart from stopped and from running services, and checks that the containers and volumes survive stop.
  For T-DCK-04 it writes a file into the data volume, removes the containers, starts again, then removes the images so
  start runs setup and builds them, and checks the file and the database each time. For the failure half of T-DCK-05,
  it adds `tests/docker/compose.lifecycle-unhealthy.yaml`, whose backend health check always fails, and expects exit
  status 3 with the last logs. With a stand-in `docker.cmd` that always fails, it checks that stop exits 0 and that
  start, status and logs exit 2, and it runs each root `.cmd` with an unknown argument through `cmd.exe`. The GPU rows
  of start are skipped, so the file needs no GPU.
- `tests/docker/Models.Tests.ps1` holds T-DCK-11. Against the project `lyrveil-models-test`, it runs the models
  command with the real `lyrveil-models` service, a test catalog and a local HTTPS server,
  `tests/docker/models-server/`, that serves generated files. With no console input and no `-AcceptModelLicenses` the
  command exits 5 and the server sees no request. With the flag, the first file's connection is cut halfway and the
  download resumes with one `Range` request; both defaults end in the volume with their catalog hashes and in
  `/models/.verified.json`, and a second run sends no request. A wrong hash and a wrong size exit 4 and leave no file
  and no `.part` file; a redirect to `http://` exits 1 and a redirect to HTTPS is followed. It also checks `-List`,
  `-Verify` (exit 4 and no record for a changed file), `-Remove` (refused for an active profile, asks first, and
  deletes nothing without an answer) and conflicting options.
- `tests/docker/Data.Tests.ps1` (P01.6d) covers `import-model`, `backup` and `restore` against the project
  `lyrveil-data-test`, started from nothing with setup. `import-model` copies a `.gguf` to `llm/` and a `.safetensors`
  to `image/checkpoints/` with the same SHA-256, and refuses an existing name, keeping the file, another extension and
  a name with a space. `backup` writes one backup into the data volume and copies it, with the same hash, to the
  backup folder set by `LYRVEIL_BACKUP_DIR` in the test's `.env`, a folder in Pester's temporary drive. `restore`
  refuses a backup whose manifest hash was changed, without asking, stopping or changing anything; without console
  input it exits 5; after a yes it stops, replaces the data, keeps the previous data in `pre-restore-<time>/` and ends
  with the ready line. T-DCK-12 (P03.7b) seeds a character with an avatar, a conversation, an application setting
  and a launch file through `tests/docker/round_trip.py`, piped to `python -` in the running backend container, backs
  up, changes all of them, deletes the backup from the volume, restores the copy in the Windows folder, and requires
  the same characters, conversations, assets with the SHA-256 of their files, settings and launch file as before, with
  the copy in the folder unchanged. Since the backend may add a daily backup at any idle moment, no test counts
  backups. Since P06.6a the three `import-model` tests are T-DCK-14, and the refusals also cover `.ckpt`, `.pt` and a
  name of 129 characters before the extension, each with its message. This version has not run yet.
- `tests/docker/Security.Tests.ps1` (P01.7b) holds T-DCK-08 to T-DCK-10. It resolves `compose.yaml` with both
  profiles and an empty env file, and checks that only the frontend publishes a port, `127.0.0.1:4200` to 8080, that no
  service uses the host network, that every service other than the frontend and `lyrveil-models` joins only internal
  networks, and that every service sets `user: "10001:10001"`. It then starts `compose.test.yaml` as the project
  `lyrveil-security-test` on port 14213 and checks that the backend, the two fakes and a one-off orchestrator
  container have no default route and cannot open a connection to `1.1.1.1:443`, while the frontend has a default
  route as the control; that the project's networks other than the edge are internal; that only the frontend has a
  published port, on `127.0.0.1`; that each container runs as `10001:10001` and each image sets that user; and that the
  frontend, backend and orchestrator images hold no `.env`, weights, database, files in `/data`, `/models` or
  `/backups`, tests or fakes, with the fakes image as the control. No check needs the Internet. It ran and passed in the P01 exit ([evidence](verification/2026-11-07-p01-exit.md)).
  Since P02.6c the test project's orchestrator is the test-only image `lyrveil/orchestrator-fakes:test`, so the file
  starts every service, builds the production `runtime` stage as `lyrveil/orchestrator:test` for the image checks, and
  takes the orchestrator-fakes image as a second control that must hold the fakes. This version has not run yet.
- `tests/docker/Orchestrator.Tests.ps1` (P02.6c) starts the production `runtime` image alone from
  `tests/docker/compose.orchestrator-test.yaml`, as the project `lyrveil-orchestrator-test`, with no Docker socket, no
  GPU, no published port and an empty env file. It checks that the image's command is `python -m
  lyrveil_orchestrator.main` and that `compose.yaml` does not override it; that the container becomes healthy under
  the health check, user and hardening of `compose.yaml`'s service, which it compares; that `/v1/health` answers 200
  without a token and `/v1/state`, `/v1/preflight` and an unknown path answer 401 without the right one (T-ORC-10);
  that the state ends in `ERROR` with `DOCKER_UNAVAILABLE` and the preflight fails its GPU row, so the real gate and
  NVML are in use and no fake; and that `import fakes` fails in the image. It has not run yet.
- `tests/docker/TextRuntime.Tests.ps1` (P02.1) builds `docker/llm/Dockerfile` as `lyrveil/llm:test`; the first run
  downloads the 629 MB KoboldCpp binary from GitHub, later runs use the build cache. It checks that the Dockerfile pins
  the version, size and SHA-256 of [Technology](technology.md), and that a build with another size or hash fails. The
  image runs as `10001:10001` and holds no `.gguf`, `.safetensors`, `.ckpt`, `.env` or single-file binary, with a
  control run. In containers with a read-only root, the tmpfs `/tmp`, no capabilities, `no-new-privileges` and no
  network, the unpacked launcher prints `1.124.2` for `--version` and lists every flag of the entrypoint in `--help`,
  the root refuses a write even from root while `/tmp` accepts one, and the entrypoint, with tmpfs folders in place of
  `/run/lyrveil` and `/models/llm`, turns the example launch file and a file with every optional flag into the
  documented command (`--print-command`), and exits 78 for an unknown, invalid, repeated or missing key, Windows line
  endings, a command substitution, a path in `MODEL_FILE`, a line that is not `KEY=VALUE`, a missing launch file and
  a missing model, and 64 for an unknown argument. It needs no GPU and no model. It has not run yet.
- `tests/docker/ImageRuntime.Tests.ps1` (P05.1) builds `docker/image/Dockerfile` as `lyrveil/image:test`; the first
  run downloads ComfyUI and about 4 GB of wheels, later runs use the build cache. It checks that the Dockerfile clones
  the commit of [Technology](technology.md), that every package of `docker/image/requirements.lock` has one version
  and at least one SHA-256, and that `torch` and `torchvision` are `+cu130` builds. The image runs as `10001:10001`
  and holds no `.gguf`, `.safetensors`, `.sft`, `.ckpt`, `.pt`, `.env` or `.git`, and no `.pth` other than Python
  path files at the top of `site-packages`; its checkpoints folder holds only the upstream placeholder. With a
  read-only root, the 4 GiB tmpfs `/tmp/comfy`, no capabilities, `no-new-privileges` and no network, the entrypoint
  started with `--cpu` and the example launch file makes ComfyUI answer `GET /system_stats` with version `0.43.0`, a
  `+cu130` PyTorch, the `cpu` device and the flags of [Runtime contracts](runtime-contracts.md). Since GPU.6 one
  container started twice, as the orchestrator starts it for every image job, must create a folder in `/tmp/comfy` at
  both starts and find its mode 1777 (written, not run yet). The entrypoint turns
  launch files into the documented command (`--print-command`), exits 78 for an unknown, invalid, off-step, repeated
  or missing key, a command substitution, Windows line endings, a line that is not `KEY=VALUE` and a missing launch
  file, and 64 for an unknown or repeated argument. It needs no GPU and no model. Since P05.3a it
  also builds the target `clip-tokens-check` with `--output type=cacheonly`, the docker-level part of T-IMG-16: every
  `clip_tokens` of the tag vocabulary must equal the count of the pinned tree's tokenizer. The file ran for the first
  time in the P05 exit on 2026-11-07 and passed after one fix in the test's own lock reader
  ([evidence](verification/2026-11-07-p05-exit.md)).

The setup tests never touch a real installation. They add `tests/docker/compose.setup-test.yaml` to `compose.yaml`,
which renames the two external volumes to `lyrveil-setup-test-data` and `lyrveil-setup-test-models`, tags every image
`:setup-test`, and puts the fakes image in place of the KoboldCpp image of `lyrveil-llm` and the ComfyUI image of
`lyrveil-image`, without their `container_name`, GPU request, volumes or health check, so setup creates the two
runtime containers with no runtime download and no GPU. The lifecycle, data and models overrides give both runtimes a
profile that no command enables, so those projects build and create no runtime, as before P02.1. Every command names the
project with `-p`, `--env-file` points at `.env.example` or at a `.env` in Pester's temporary drive, and setup refuses
to start unless the resolved configuration names only the test volumes. The test removes the project and both volumes
afterwards and fails if a volume is left. Only `Data.Tests.ps1` uses a backup folder, its own in Pester's temporary
drive, and never `%USERPROFILE%\Documents\Lyrveil\backups`.

The lifecycle tests use `tests/docker/compose.lifecycle-test.yaml` the same way, with the volumes
`lyrveil-lifecycle-test-data` and `lyrveil-lifecycle-test-models` and the tag `:lifecycle-test`. Because they start
containers, the override also resets `container_name`, so no container takes the name of an installation's; it publishes
the app on `127.0.0.1:14210` instead of 4200; and it resets `env_file`, so no container reads the repository's `.env`.
The file refuses to run unless the resolved configuration has the test project, the test volumes, no `container_name`
and port 14210.

The models tests use `tests/docker/compose.models-test.yaml` the same way, with the volumes
`lyrveil-models-test-data` and `lyrveil-models-test-models`, the tag `:models-test`, no `container_name` and no
`env_file`. The override points `lyrveil-models` at the test catalog and the test CA that `models-server` writes into
the project's own volume, so every URL the tool opens is the test server on the download network; nothing reaches the
Internet. The file refuses to run unless the resolved configuration has the test project, the test volumes, no
`container_name`, and the test catalog and CA.

The data tests use `tests/docker/compose.data-test.yaml` the same way, with the volumes `lyrveil-data-test-data` and
`lyrveil-data-test-models`, the tag `:data-test`, no `container_name`, no `env_file` and port 14212. The file refuses
to run unless the resolved configuration has the test project, the test volumes, no `container_name` and port 14212,
and unless the backup folder resolved from its `.env` is the one in the temporary drive.
`tests/scripts/DataFiles.Tests.ps1`, at the `unit` level, checks without Docker that the one-off containers have no
network and mount the Windows folder read-only, how `LYRVEIL_BACKUP_DIR` is resolved, that the backup copy of start
and stop turns an invalid folder into a warning reason, and that wrong arguments, extensions and names are refused
before any Docker call. `tests/docker/Lifecycle.Tests.ps1` passes its own backup folder in the temporary drive to
start and stop, because setup writes its `.env` from the template, whose empty `LYRVEIL_BACKUP_DIR` means the
Documents folder. `backend/tests/unit/test_daily_backup.py` covers the daily backup: one per day of `LYRVEIL_TZ`, only
after a check interval without commits and without a running job, pruning to 7, one hour of wait after a failure, and
a database copy that stays consistent while another connection writes.

### The `e2e` level

Delivered in P01.8c ([evidence](verification/2026-11-06-e2e-level.md)). [`scripts/lib/EndToEnd.psm1`](../scripts/lib/EndToEnd.psm1) runs it from Windows with Node.js 24 and Docker:

1. It runs `npm ci --ignore-scripts` in `tests/e2e/` when `node_modules` is missing or older than the lock, then `npx playwright install chromium`, which downloads the browser of the locked Playwright 1.64.1 into the user's Playwright cache.
2. It removes any earlier project named `lyrveil-test`, so the volumes start empty, then runs `docker compose -p lyrveil-test -f compose.test.yaml up --build --detach --wait` for `lyrveil-frontend` (which starts `lyrveil-backend`), `lyrveil-orchestrator`, `lyrveil-llm` and `lyrveil-image`. Since P02.6c the orchestrator serves the control API over its fakes and has a health check, so `up --wait` waits for it too.

   Since P06.6b the backend also depends on `lyrveil-models-seed` (`service_completed_successfully`), a one-shot service
   of the backend image that runs as root without a network and writes, into the empty `models` volume, a sparse file
   of the exact catalog size for every text and image profile (so each reads as `verified` without a download) and the
   small file `llm/e2e-custom.Q4_K_M.gguf`, which `tests/e2e/specs/models.spec.mjs` registers. Delivered, not yet run
   against Docker: the seed service and `up --wait` with a service that ends.

   The P06.6b specs, also not run yet: `models.spec.mjs` lists the profiles with licence, tier and state, chooses a
   second text profile, applies it with **Applica ora** until the reload job ends, registers the custom file, removes
   the registration and puts the first profile back, and checks that a profile above the tier cannot be chosen;
   `resources.spec.mjs` checks the GPU state code, the 12282 MiB gauge of the fake NVML, the queue and the active model.
   Their unit tests are `model-settings-store.spec.ts`, `model-settings-screen.spec.ts` and `resources-store.spec.ts`
   (including the live `gpu.state` update), and `backend/tests/integration/test_ai_resources.py` for the route.
3. It runs `npx playwright test` against `http://127.0.0.1:14200`, or the port in `LYRVEIL_TEST_HTTP_PORT`. It refuses 4200.
4. In a `finally` block, whatever happened before, it runs `docker compose -p lyrveil-test ... down --volumes --remove-orphans`, then lists the containers and volumes that still carry the project's label. The level fails if any is left or if the removal cannot be checked.

The level touches only the project `lyrveil-test`, named with `-p` so no `COMPOSE_PROJECT_NAME` can redirect it, and never `lyrveil-data`, `lyrveil-models` or the backup folder. The smoke test, `tests/e2e/specs/smoke.spec.mjs`, opens the empty app, checks the redirect to the chat list, the title and the heading, and fails on any [axe-core](https://github.com/dequelabs/axe-core) violation. `tests/e2e/specs/security-headers.spec.mjs` is T-SEC-02. `tests/e2e/specs/reply-stream.spec.mjs` (P02.6c) sends a message through the backend API from the page, receives the reply's `typing.started`, `message.delta` (each at the offset where the text before it ends), `typing.stopped` and `message.completed` frames over `/ws` through nginx, finds the same text stored in the history, and reads the orchestrator's `/v1/events` with `docker compose exec` to see the reply job's lease granted and released with `ok`. `compose.test.yaml` gives the backend `LYRVEIL_HTTP_PORT` equal to the test port, because the backend refuses any other port in `Host` and `Origin`. The Pester cases "The e2e level" in `tests/scripts/TestCommand.Tests.ps1` check the order of the steps and the cleanup with stand-in commands; they ran and passed in the `unit` level of the P01 exit ([evidence](verification/2026-11-07-p01-exit.md)).

### Pinned tools

The command installs nothing machine-wide. It fetches PSScriptAnalyzer 1.24.0 and Pester 5.7.1 from the PowerShell Gallery into the Git-ignored `.tools\` folder. It checks the SHA-256 of each package against [`scripts/lib/tools.psd1`](../scripts/lib/tools.psd1) before extracting it, and refuses a package that differs. The hashes were recorded at the first download on 2026-11-06. `LYRVEIL_TOOLS_DIR` overrides the folder. The inbox Pester 3.4.0 is never used.

### The `lint` level

`lint` runs after every change, through `cmd.exe` as `"scripts/lyrveil.cmd" test lint` or from Git Bash, so it must stay fast and pass on every commit. It runs the parts below, then the frontend gates described under [Commands](#commands), prints a line for every check, and exits 1 if any check fails.

Documentation checks, in [`scripts/lib/DocumentationChecks.psm1`](../scripts/lib/DocumentationChecks.psm1), scan the Markdown files Git tracks. They also scan new files that `.gitignore` does not exclude, so a check before a commit sees what the commit adds. They read every file as UTF-8 and fail on:

- a relative link to a file or folder that is not in the repository, or that leaves it, and an anchor that no heading of the target produces, using GitHub's anchor rules. Links in code blocks and inline code are skipped, and absolute URLs are not requested;
- an unfinished-work marker or filler text. The list of markers is in the module;
- a requirement or acceptance criterion of [Requirements](requirements.md) that heads no complete row of [Traceability](traceability.md), or a traced identifier that Requirements does not define;
- a test identifier used in any document but missing from the [catalog](#catalog), and a catalog test that no `Exit:` line of the [roadmap](roadmap.md) names. Ranges such as `T-DCK-01 to T-DCK-11` count for every member;
- an ADR without its `Status:` line, one of its four sections, or its link in the [index](decisions/README.md);
- a `json` code block that does not parse.

PSScriptAnalyzer then checks every Git-tracked `.ps1` and `.psm1` with [`scripts/PSScriptAnalyzerSettings.psd1`](../scripts/PSScriptAnalyzerSettings.psd1): the default rules at the Error and Warning severities, without `PSAvoidUsingWriteHost`, because the scripts are console commands.

Last, [`scripts/lib/PythonComponents.psm1`](../scripts/lib/PythonComponents.psm1) builds the `test` stage of `backend/Dockerfile` as `lyrveil/backend-test:local` and of `orchestrator/Dockerfile` as `lyrveil/orchestrator-test:local`, and runs `ruff check .`, `ruff format --check .` and `mypy --strict src tests` in containers of each without network access. Each gate prints its exit status and fails the level when it is not 0. A failed image build also fails the level. A component without its Dockerfile is skipped, and a repository without either, such as the fixtures of the Pester tests, skips this part. The `unit` level builds the same stages and runs `pytest tests/unit` in each.

The other checks of the [2026-11-01 documentation checks](verification/2026-11-01-documentation.md) are not part of `lint`. Their lists of required names stayed in the temporary script, the secret scan moves to `gitleaks`, and the Mermaid parser runs in CI (P01.8d, [Continuous integration](#continuous-integration)).

[`tests/scripts/TestCommand.Tests.ps1`](../tests/scripts/TestCommand.Tests.ps1) builds fixture repositories in Pester's temporary drive. It shows that each check fails on its own defect. It also runs a copy of `scripts\lyrveil.cmd test lint` in a fixture: the command exits 0 on a consistent fixture, and non-zero on a tracked broken link and on a PSScriptAnalyzer finding. Other cases check that `lint` refuses arguments and that `unit` with pytest arguments fails when no Python component exists. With a stand-in `docker.cmd` first on `PATH`, three cases check which component runs pytest: `integration` skips a component without `tests/integration`, a named test runs only in the component that holds it, and a test that no component holds fails the level.

[`tests/scripts/ConsolePrompt.Tests.ps1`](../tests/scripts/ConsolePrompt.Tests.ps1) starts Windows PowerShell with `y` piped to it, and again with `-NonInteractive`, and checks that `Read-Confirmation` answers no at once both times. It is written and has not run yet.

## Layout

```text
backend/tests/{unit,integration,contract}/    contract is planned
orchestrator/tests/{unit,integration}/
frontend/src/**/*.spec.ts
tests/
  scripts/           Pester tests of scripts\lyrveil.ps1 and its modules
  fakes/             fake_kobold, fake_comfy, fake_docker, fake_nvml
  fixtures/          Demo characters, synthetic conversations, recorded runtime responses
  e2e/               Playwright 1.64.1 and @axe-core/playwright 4.14.0: package.json, playwright.config.mjs, specs/
  docker/
  gpu/
  quality/           Consistency.Tests.ps1 (T-QLT-05), Extraction.Tests.ps1 (T-QLT-06), ImageGrid.Tests.ps1 (T-QLT-02, T-QLT-07)
  quality/consistency/   script.json, the scripted conversations of T-QLT-05 and T-QLT-01
  quality/italian/   not used: T-QLT-01 reads the replies of the consistency conversations
  quality/images/    planned
```

The 40 windows of T-QLT-06 are `backend/tests/fixtures/extraction_eval/windows.json`, beside the probe that reads them
in the backend's test stage.

### Fake runtimes

Delivered in P01.8b. Their tests run in the orchestrator's test stage with the `unit` level ([evidence](verification/2026-11-06-fake-runtimes.md)).

- `fake_kobold` and `fake_comfy` are HTTP servers on the standard library that implement exactly the contracts in [Runtime contracts](runtime-contracts.md): KoboldCpp's eight endpoints, and ComfyUI's five HTTP endpoints and its `/ws` WebSocket. A test reads the endpoint tables of that page and compares them with the routes of each fake, and checks that any other path answers 404 and another method 405. They support scripted replies, delays, invalid JSON, out-of-memory errors and crashes.
- A test scripts a behavior with `fake.script("POST /api/v1/generate", Step(action="oom"))`. Each `Step` is used once, by the next request to that route. The container reads the same steps from the `FAKE_SCRIPT` environment variable as JSON. A fake has no control endpoint, so it serves nothing that the contract does not list.
- `fake_comfy` plays a job on the WebSocket of its `client_id` with the messages of the contract. The steps scripted on `WS /ws` shape the next job: an out-of-memory error, an unparsable frame, a crash after N messages or delays. `POST /interrupt` ends the running job with `execution_interrupted`.
- The out-of-memory reply of `fake_kobold`, a 500 answer naming CUDA and a stream that ends with `finish_reason` "error", is an assumption: the contract does not record how KoboldCpp reports it.
- `fake_docker` and `fake_nvml` implement `ContainerGate` and `GpuProbe`. `FakeContainerGate` subclasses the real gate without opening the socket, keeps the state of both containers, refuses a missing or foreign container and a name outside the allowlist, and raises queued failures with `fail`. `FakeGpuProbe` models a card shared with other programs: external memory use, scripted between reads, queued and permanent failures, and a count of reads.
- They live only in `tests/fakes/` and in `compose.test.yaml`, and never enter a production image. `tests/docker/TestFakes.Tests.ps1` lists the file system of each production image and fails if `fakes` or a fake's name appears. It also lists the image built from `tests/fakes/Dockerfile`, to show that the check can find them. Only the test stages of the orchestrator and, since P02.3a, the backend, and that image, copy `tests/fakes/`. The backend's `tests/unit/test_koboldcpp_runtime.py` runs `KoboldCppRuntime` against `fake_kobold` ([Backend](backend.md#text-runtime)), and since P05.2a `tests/unit/test_comfyui_runtime.py` runs `ComfyUiRuntime` against `fake_comfy` over HTTP and WebSocket and holds T-IMG-05 ([Backend](backend.md#image-runtime)).
- `fake_orchestrator` (P02.6c) is the command of the test-only `fake-runtime` stage of `orchestrator/Dockerfile`, the image `lyrveil/orchestrator-fakes:test` of `compose.test.yaml`: it passes `FakeContainerGate` and `FakeGpuProbe` to `lyrveil_orchestrator.main.run`, so the real control API and lifecycle manager grant leases without a Docker socket or a GPU. The fake gate reports the text runtime healthy once started, while the `fake_kobold` container answers the backend. `orchestrator/tests/unit/test_main.py` checks that `main` always passes `ContainerGate` and `NvmlProbe`, that the settings hold only the token and the log level, and that no module of `lyrveil_orchestrator` imports `fakes`.
- `compose.test.yaml` starts the three core services with the two HTTP fakes as the services `lyrveil-llm` and `lyrveil-image`, and the orchestrator over its fakes. It has its own project name `lyrveil-test`, its own port, its own volumes (`data` empty, `models` filled once by the one-shot service `lyrveil-models-seed` since P06.6b, see below), its own `:test` image tags and inline settings, and no `container_name`. The same Docker test reads the resolved configuration and fails if it names an external volume, a host folder, the Docker socket, `gpus` or port 4200.
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
| T-CHR-11 | Upload: valid, wrong bytes, too large. Metadata removed, crop, single primary avatar. Delivered: `backend/tests/integration/test_avatars.py` (PNG text and EXIF, JPEG EXIF, thumbnail, crop inside and outside the image, primary switch, primary delete refused, purge removes the files, `avatar_url` on the conversation list) and, in the browser, `tests/e2e/specs/avatar.spec.mjs` (refused file with the Italian message, upload with preview, crop, primary choice, avatar in the chat list; written in P03.4b and not run yet) and `asset.model.spec.ts` | Integration, E2E | P03 |
| T-CHR-12 | Avatar generation through the shared image job. Delivered (P05.5a, P05.5b): in `backend/tests/integration/test_image_modes.py`, a generated avatar and scene join the assets (the avatar as primary) and keep their files after the asset is deleted, and an avatar request runs the `image_request` job through its three stages, publishes `image.progress` for the prompt, loading, first pass, second pass and saving, and ends as the character's primary avatar. In the browser, the third test of `tests/e2e/specs/image-request.spec.mjs` presses **Genera un avatar**, sends the real request to the backend and follows the job to a primary avatar; the drawing is played by routes, so the steps do not depend on the timing of the mode switch that runs the real job since P05.7. It passed in the P05 exit on 2026-11-07 after one fix in the spec itself ([evidence](verification/2026-11-07-p05-exit.md)). The GPU part is still open: it needs a run on the reference machine | Integration, E2E, GPU | P05 |
| T-CHR-13 | Full creation flow in the browser | E2E | P03 |
| T-CHR-14 | Voice and wardrobe fields: `grammatical_gender` with its CHECK, `address_form`, `narration`, `emoji`, example exchanges, `avoid`, identity slots against the vocabulary, outfits with reserved keys refused. Round trip through export and import | Unit, integration | P03 |

Delivered in P03.6a: T-CHR-02 at the draft entry point is
`test_a_description_of_a_minor_is_refused_before_the_model_runs` in `backend/tests/integration/test_character_drafts.py`
(an age under 18 in digits and in words, a minor term and childhood next to a sexual term in the description answer
`422 CONTENT_POLICY_BLOCKED` and queue no job; a job queued past the route fails with `CONTENT_POLICY_BLOCKED` before
any generation). The same file checks the guard on the draft's output: a refused draft is
repaired once with the guard's reason, and a second refusal fails the job with `CONTENT_POLICY_BLOCKED`. The age of
the draft's output stays covered by the T-CHR-10 tests of the same file (16, then 15). T-CHR-02 at the API and the
database is `test_an_age_under_18_is_refused_by_the_api_and_by_the_database` in `test_characters.py`, and at import
`test_a_minor_is_refused_at_import` in `test_character_exchange.py`. In this change
`scripts\lyrveil.cmd test unit tests/unit/test_content_guard.py` passed (107 passed) and
`scripts\lyrveil.cmd test unit tests/integration/test_content_guard.py tests/integration/test_character_drafts.py`
passed (16 passed); the full `unit` and `integration` levels were not run. Since this change `backend/tests/unit` and
`backend/tests/integration` are packages (`__init__.py`), so a unit and an integration test file may share a name.

Delivered in P03.5: T-CHR-05, T-CHR-06 and the round trip of T-CHR-14 are
`backend/tests/integration/test_character_exchange.py` (a round trip with a PNG, a JPEG and a WebP checks the card, the
primary avatar and the hashes; refusals of a newer format, another format, text that is not JSON, unknown fields in the
file, in the card and in an asset, an age of 17, an unknown slot tag, a reserved outfit key, a wrong hash, bad base64,
non-image bytes and two primary avatars, each leaving the character count unchanged; the 64 MB limit). The test file
was written without being run in the change that delivered it. The JSON Schema is checked for shape and published
content, and every export is validated with the model the schema is generated from; no independent JSON Schema
validator is installed, so the schema was not run through one.

Delivered in P03.2b: T-CHR-04 is `test_duplicate_copies_the_card_but_not_conversations_or_memories` in
`backend/tests/integration/test_characters.py`, beside the archive, unarchive and purge tests of the same file (the purge
test checks every table, the search index and the jobs; another character stays untouched). The profile has unit specs
(`character-profile-screen.spec.ts`, `version-diff.spec.ts`) and the end-to-end spec
`tests/e2e/specs/character-profile.spec.mjs`, which covers compare, restore (T-CHR-08), duplicate, archive and purge
through the browser. The end-to-end spec was written but not run in this change.

Delivered in P03.3: T-CHR-10 is `backend/tests/integration/test_character_drafts.py`. It queues a draft with
`POST /characters/drafts`, runs it with the real `JobScheduler` under a granted llm lease, `CharacterCreatorService` and
`KoboldCppRuntime` against `fake_kobold` over HTTP, and reads it with `GET /jobs/{id}`: valid output completes the job
with a card and no character; an age of 16 is repaired once with the error and the refused output in the prompt; output
that is not JSON then an age of 15 fails the job with `STRUCTURED_OUTPUT_INVALID` after exactly two generations. The same
file checks that a draft over a character keeps its locked trait and field and leaves the stored card at version 1, and
the route refusals. `backend/tests/unit/test_character_draft.py` holds the schema checks: `CharacterDraft` has every
`CharacterCard` field except the documented exclusions, with the same JSON Schema for each, and the grammar generated
from it, read by a small GBNF matcher in the test, accepts a whole draft and refuses an unknown or missing key, a value
outside a closed list, too many outfits and an age outside 18 to 120. It also covers the T-CHR-09 case of a draft (locks,
seed, English tags kept). `character-creator-screen.spec.ts` covers the assist panel. In this change both backend files
passed (`scripts\lyrveil.cmd test unit tests/unit/test_character_draft.py`, 7 passed, and the same command with
`tests/integration/test_character_drafts.py`, 5 passed); the frontend spec was not run.

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

Delivered in P04.2a: `backend/tests/integration/test_memory_extraction.py` runs the extraction job through the real
`JobScheduler` under a granted llm lease, with `MemoryExtractionService` and a scripted in-process text runtime, on a
migrated SQLite file. T-MEM-01: valid output stores three memories with their sources (active variant and content hash
of each message), index rows, an episode with its sources, the completed run with `raw_output` and the cursor at 10,
and drops or defers the items it does not apply. T-MEM-02: output that is not JSON, then a repair with an importance of
9, fails the attempt with `STRUCTURED_OUTPUT_INVALID`, stores no memory, keeps the cursor at 0 and the refused text in
the `failed` run, waits for the 1-minute backoff, then succeeds on attempt 2 with the same run row. T-MEM-03: running the
job again after its commit, and running the same window after the cursor was moved back, generate nothing and add no
row. T-MEM-17, scheduler half: in image mode and with no runtime ready the job is not picked and no lease is asked. The
same file checks the trigger (nine messages are not due, ten are, one job per chat at a time, a significant phrase needs
two messages) and that the fifth reply of a chat queues the job. `backend/tests/unit/test_memory_extraction.py` holds the
policy half of T-MEM-17 (the only stage needs `llm`, background class) and the schema, grammar, worked example, window,
hash, trigger, pattern, fingerprint and prompt checks. In this change the backend part of `scripts\lyrveil.cmd test unit
-k "memory_extraction or memory_text or character_draft or job_policy or import_rules"` passed (66 tests; the filter
selects no orchestrator test, so that part exits 5), and `scripts\lyrveil.cmd test integration` passed (233 backend,
200 orchestrator).

Delivered in P04.2b: `backend/tests/unit/test_memory_consistency.py` holds T-MEM-04 (the same fingerprint confirms the
memory in use; a near duplicate of the same kind and subject, at a Jaccard of at least 0.8 and below 1, is confirmed, while another kind,
subject or value is not) and T-MEM-14 (an inferred update leaves a stated or confirmed memory as it is and is stored
uncertain in its group; a stated user fact without a user source becomes inferred and capped at 0.6; what the
character says about itself becomes fictional), with guard 3, the update and correction checks, locks, temporary
items and identity candidates. `backend/tests/integration/test_memory_extraction.py` runs consecutive windows of one
chat through `MemoryExtractionService` for T-MEM-10 (the target becomes `superseded` at version 2 with `valid_to` and
`superseded_by_id`, one revision with the earlier row, the run id and reason `update`, its sources kept, and the next
extraction prompt shows only the new fact), T-MEM-11 (both `uncertain` in one group, one revision, one
`memory.conflict` event, and both shown `[incerto]` in the next prompt), T-MEM-12 (a temporary item aimed at a lasting
city is stored next to it, expires 7 days later with a `consistency` revision, and the lasting one stays active and
shown), T-MEM-13 (a user correction supersedes and is stored `user_confirmed` at 1.0) and the extraction half of
T-CON-05 (identity candidates are stored, never shown, never change their target, the `characters` row or
`character_versions`). Applying a candidate to the profile is the Memory Studio's (P04.9), so the second half of
T-CON-05 is not covered yet, and the reply-prompt half of T-MEM-10 and T-MEM-11 waits for retrieval (P04.3, P04.5). In
this change `scripts/lyrveil.cmd test unit tests/unit/test_memory_consistency.py tests/unit/test_memory_extraction.py
tests/unit/test_memory_text.py tests/integration/test_memory_extraction.py tests/integration/test_memory_index.py`
passed (70 tests) and `scripts/lyrveil.cmd test lint` passed; the full levels were not run.

Delivered in P04.2c: `backend/tests/integration/test_memory_extraction.py` also holds T-MEM-08 (an edit through
`MemoryService` appends one revision with actor `user` and reason `edit` holding the earlier row, moves the memory to
version 2 with a new fingerprint, refreshes the index row, keeps the sources, writes nothing for an unchanged edit, and
refuses a duplicate text and an importance of 9) and T-MEM-09 (a deletion leaves one tombstone with hashes and no text
and removes the memory, its sources and its index row; the same source, a later message with the same content and a
reworded memory with the same subject and value are blocked; a later source with new content stores the memory again).
`backend/tests/unit/test_memory_consistency.py` covers `is_blocked`. In this change `scripts/lyrveil.cmd test unit
tests/unit/test_memory_consistency.py tests/integration/test_memory_extraction.py` passed (24 tests); the full levels
were not run.

Delivered in P04.9a: `backend/tests/integration/test_memory_studio_api.py` drives the Memory Studio routes over data the
real extraction pipeline wrote (it reuses the `Chat` helper of `test_memory_extraction.py`). It covers the memory list
with its filters, search and pages; one memory with its source message and confidence; lock, unlock, confirm and delete;
T-MEM-08 through the API (an edit moves the memory to version 2 and the history lists the text it replaced, with a
duplicate refused with `409` and an importance of 9 with `422`); T-MEM-11 through the API (both memories `uncertain` in
one conflict group, each listing the other, and a `contradiction` revision); the episode with its sources and links, the
relationship state in words with the numbers apart, its change log with reason and source seq, and a summary
checkpoint with its `source_seqs`; and an empty state and a manual memory. `memory-store.spec.ts` and
`memory-studio-screen.spec.ts` (frontend unit) cover the requests of each filter and action, the label of a
contradiction, the sources and history under a card, and the three data tabs, and `tests/e2e/specs/memory-studio.spec.mjs`
runs the hand-written-memory flow in the browser (the fake runtime produces no extraction output, so the end-to-end spec
cannot show sources, events, summaries or a contradiction). In this change `scripts/lyrveil.cmd test unit
tests/integration/test_memory_studio_api.py` passed (4 tests) and `scripts/lyrveil.cmd test lint` passed (it includes the
strict frontend build). The frontend unit specs and the end-to-end spec were written but not run: no allowed command
runs one frontend spec alone, and the full levels were not run.

Delivered in P04.9b: `backend/tests/integration/test_diagnostics.py` runs the real `ChatInferenceService` and
`ContextBuilder` on a migrated database (only the text runtime is the fake of `test_reply_streaming.py`) and covers
R-STU-04 with T-MEM-21's assembly: the documented default is off and both reads answer `404`; with the mode on, a reply
built from a message that matches a memory shows the nine block counts, the budgets, the memory with its text, label,
score and the nine score parts, the empty summaries and the prompt text; a reply built while the mode was off keeps no
prompt text; turning the mode off hides every record and turning it on again shows them; only the newest 200 rows of a
conversation are kept; and a marker in the message reaches the stored prompt text but never the log. The migration test
covers revision `0012`. `memory-store.spec.ts` and `memory-studio-screen.spec.ts` cover the setting, the tab that is
drawn only when it is on, the list of replies, the refusal after the mode is turned off, and the blocks, budgets,
memories with score parts, summaries and absent prompt text of a chosen reply; `tests/e2e/specs/memory-studio.spec.mjs`
runs the flow in the browser. In this change `scripts/lyrveil.cmd test unit` with `tests/integration/test_diagnostics.py`,
`test_migrations.py` and `test_memory_studio_api.py` passed (34 tests), the same with `test_reply_streaming.py` and
`test_characters.py` passed (59 tests), and `scripts/lyrveil.cmd test lint` passed. The frontend unit specs and the
end-to-end spec were written but not run, for the reason above.

Delivered in P04.3: `backend/tests/integration/test_memory_retrieval.py` runs `FtsMemoryRetriever` on a migrated SQLite
file. T-MEM-05: the order follows the weighted score, whose parts each item keeps (two matches normalized to 0.5 and
1.0, a locked memory joining with no match, an unrelated one never a candidate); the topic and the previous selection
raise the score, the latter by exactly 0.05; a budget, `max_items`, six items per kind and three episodes bound the
selection; one memory per subject wins, a conflict group enters whole and labeled `[incerto]` or not at all; only active
and uncertain memories are seen, superseded ones join under past intent as `[passato]`, and expired, contradicted,
invalidated and identity-candidate rows never do; episodes render as `[episodio]`, and a query of stop words or FTS
syntax leaves only the standing memories. T-MEM-15 and T-CON-02: two chats of one character see nothing of each other,
and the shared scope is read only with the query's flag and the conversation's `shares_memory = 1`. T-CON-01: character
B finds nothing of character A, even with A's conversation id, shared memory, a locked memory and the exact words.
T-MEM-22, listed at the Quality level, runs here because it needs no model: on
`backend/tests/fixtures/memory_retrieval/evaluation.json` (3 conversations, 60 questions) recall at 8 was 58 of 61,
95.1 %, against the 80 % target; the misses are the three paraphrase questions. The same file checks the use counters.
`backend/tests/unit/test_memory_retrieval.py` holds the pure rules (normalization, recency floor, cap at 1, normalized
weights, labels, Italian past-intent cues). In this change `scripts/lyrveil.cmd test unit
tests/unit/test_memory_retrieval.py tests/integration/test_memory_retrieval.py` passed (20 tests) and
`scripts/lyrveil.cmd test lint` passed; the full levels were not run.

Delivered in P04.4: `backend/tests/integration/test_summaries.py` runs the `summary_checkpoint` job through the real
`JobScheduler` under a granted llm lease, with `SummaryService` and a scripted in-process text runtime, on a migrated
SQLite file (chunks of 20 and a tail of 10, to keep the chats short). T-MEM-16: a chat of 30 messages stores one level-0
checkpoint for 1 to 20 with its version, token count (from the runtime), prompt version and cited messages (one outside
the range dropped), moves `last_summarized_seq` to 20 and keeps the newest ten messages out of the prompt; a chat of 130
stores six contiguous checkpoints, then one level-1 rollup for 1 to 80 whose prompt holds only the oldest four, with the
six rows left unchanged; a range summarized again takes version 2 and the first row becomes `superseded` with its text
intact; an UPDATE of the text is refused by the table and only the status can change; a run whose messages changed
while the model ran writes nothing and leaves the cursor; invalid output (twice) writes nothing and fails with
`STRUCTURED_OUTPUT_INVALID`, retryable; a payload range that is not valid fails for good. T-MEM-17, scheduler half: in
image mode and with no runtime ready the job is not picked and no lease is asked. `backend/tests/unit/test_summaries.py`
holds the policy half of T-MEM-17 and the pure half of T-MEM-16: the chunk rules (tail, 20 or 40 messages, 2400 tokens,
passed-over and pending messages), the rollup rules (five do not merge, six merge the oldest four, merged rows do not
count again, a gap splits a run, superseded and invalidated rows are ignored, six rollups make level 2) and the choice
of summaries for a prompt (budget, contiguity, gaps, rollup of up to 200 tokens, invalidated never used), plus the
schema, grammar, worked example and prompts. In this change `scripts/lyrveil.cmd test unit tests/unit/test_summaries.py
tests/integration/test_summaries.py tests/integration/test_migrations.py` and the reply, extraction and index tests
passed, and `scripts/lyrveil.cmd test lint` passed; the full levels were not run.

Delivered in P04.5: `backend/tests/integration/test_context_builder.py` runs `ContextBuilder` on a migrated SQLite file
with `FtsMemoryRetriever`, four stored checkpoints, 24 memories, a full card and a fake runtime whose tokenizer counts a
fixed number of tokens per word. T-MEM-21: at 8192 and 4096 the prompt fits `window - band - 64` with no reduction,
eight examples, the four checkpoints and the blocks that are never removed; at 8192 every message after the checkpoints
fits and the room left widens block 5, at 4096 the history is the newest messages that fit, with the gap before them
reported; a tokenizer that needs twice the tokens keeps fewer turns, and the sent prompt is the one counted whole with
`special`; at 4096 with three tokens a word and at 1536 the reductions follow the documented order, each spent before
the next, the history keeps its floor unless the last step ran, and the blocks never removed stay; a reply through
`ChatInferenceService` sends the band's `max_length` 240, the summary, the memories and the anchor, and records the use
of exactly the memories shown. `backend/tests/unit/test_context_builder.py` holds T-CON-11 (the four bands and the
`llm.max_length` cap set `max_length` and `available`; the anchor's lines with the band's text, two main traits and
at most two notes, as the last block before the current message; block 3 shows examples in saved order and stops
before the first that would pass 500 tokens, and a window 30 tokens short cuts exactly the last one), a current message
over 2048 tokens cut in the prompt only, and T-MOD-05 with the full context for each family (two replies with other
memories and notes share the stable text and the history, and no old turn carries the frame). In this change
`scripts/lyrveil.cmd test unit tests/unit/test_context_builder.py` (17 passed), the same command with
`tests/integration/test_context_builder.py` (5 passed), and the formatter, summary, reply-streaming, scenario,
extraction, retrieval, conversation and message files (119 passed) passed, and `scripts/lyrveil.cmd test lint`
passed; the full levels were not run.

Delivered in P04.7: `backend/tests/integration/test_memory_invalidation.py` changes messages through `MessageService` on
a migrated SQLite file and runs the queued jobs with a scripted runtime. T-MEM-19, edit: the memory resting only on the
edited message becomes `invalidated` with a revision and keeps its source, one with another source loses the stale row
and 0.1 of confidence, the episode and the state row citing it are invalidated (the state replays to the same trust, no
mood), the level-0 and level-1 checkpoints covering it are invalidated, and a re-evaluation of seq 1 to 9 and a summary
of 1 to 5 are queued; the re-evaluation, which shows no invalidated memory, brings the memory and episode back on their
rows with the new variant as source, skips the relationship event a remaining row cites and restores the mood, and the
summary job writes version 2. T-MEM-19, deletion: deleting the message of an update invalidates the new fact and makes
the superseded one active again with a revision, and an empty re-evaluation leaves it so. T-MEM-20: editing the source
of a locked memory changes only `needs_review`, with no revision, and the re-evaluation stores the new value uncertain
for review and leaves the locked row as it was. T-MEM-18: a run cut by a crash during generation leaves the memory
tables and both cursors as the previous run left them; after a new engine on the same file and
`recover_interrupted_jobs`, the job runs through the real `JobScheduler`, the same run completes with attempt 2 and
the cursor reaches 20. `backend/tests/unit/test_memory_consistency.py` adds the age check of `decide` and
`after_source_change`. In this change `scripts/lyrveil.cmd test unit` with the new file and the consistency unit file
passed (18 tests), the same command with the extraction, summary, message, reply-streaming, state, retrieval, context
builder, queue, scenario, app, import-rule and policy files passed (157 tests), the OpenAPI document and contract tests
passed (5), and `scripts/lyrveil.cmd test lint` passed; the full levels were not run.

### Consistency

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-CON-01 | A fact told to character A never reaches character B's context | Integration | P04 |
| T-CON-02 | Two chats of one character are isolated by default | Integration | P04 |
| T-CON-03 | A state change in one chat leaves another chat unchanged | Integration | P04 |
| T-CON-04 | Canonical data hash is unchanged after 1000 synthetic messages, extraction and summaries | Integration | P04 |
| T-CON-05 | `identity_candidate` is not applied until the user applies it | Integration | P04 |
| T-CON-06 | Deltas are bounded per event, run and day, and always have a reason and sources | Unit | P04 |
| T-CON-07 | Conflicting visual tags are removed. In `backend/tests/unit/test_visual_consistency.py` (P05.3b) | Unit | P05 |
| T-CON-08 | Reply checks V1 to V13 on fixed texts, including quotations that V11 must skip | Unit | P04 |
| T-CON-09 | Reply normalizer N1 to N5 on fixed raw replies from each template family, in one piece and split into stream chunks at every position. The delivered text is identical in both cases | Unit | P04 |
| T-CON-10 | A V6, V10 or V11 hit aborts the stream, publishes `message.restarted` and regenerates once with the note in the anchor. A second hit is delivered and recorded. Adaptive notes start, last 3 replies and stay at most two | Integration | P04 |
| T-CON-11 | Length bands set `max_length` and the reply reserve, capped by `llm.max_length`. The anchor holds the band's text. Block 3 renders examples in saved order and stops before the 500-token maximum | Unit | P04 |

Delivered in P04.6: `backend/tests/unit/test_character_state.py` holds T-CON-06 on the pure rules: the magnitudes give 2,
4 and 8, and `large` gives 4 without an episode of importance 4; one run moves a dimension by a net 8 at most while each
row keeps its requested delta; one local day moves it by 15 at most, and the same rows split differently in
`Europe/Rome` and UTC around midnight; values stay from 0 to 100; and no row can be asked for without a reason and a
source message, a magnitude's size or a known dimension. It also holds the pure half of T-MEM-23: 300 seeded sequences
of 30 steps of runs, edits (invalidation, then a run that cites the edited message again) and deletions (invalidation),
after each of which the replay of the remaining rows equals an independent reading of the rules that moves one point at
a time, every run and local day stays within its limit, and new ids change nothing; plus the text events of a state
update and block 4's words (no number, the bands, the threads as details).
`backend/tests/integration/test_character_state.py` runs `MemoryExtractionService` with a scripted runtime on a migrated
SQLite file: the extraction half of T-CON-06 (six rows with their requested and applied values, reasons and sources,
one event outside the window dropped, the topic deferred, the state row equal to the replay), T-CON-03 (two chats of one
character: changes, a closed thread and the next extraction prompt of one chat leave the other chat's history rows,
state row and block 4 identical, and invalidating with the other chat's messages changes nothing), and the database half
of T-MEM-23 (6 seeded sequences of 20 steps through `CharacterStateManager`: after each step the stored row equals the
replay of the rows read back, and its values equal the independent reading over the changes asked for and not
invalidated). T-MEM-01 now also checks the state row of its one valid relationship event. Since nothing called
`invalidate` on a real message edit or deletion before P04.7, T-MEM-23 drives the invalidation directly. In this change
`scripts/lyrveil.cmd test unit` with both new files, the extraction, context builder, reply streaming, summary, index and
migration files passed (113 tests), and `scripts/lyrveil.cmd test lint` passed; the full levels were not run.

Delivered in P04.7: `backend/tests/integration/test_memory_invalidation.py` holds T-CON-04: a character with a trait and
a visual profile, 1000 synthetic messages, all 84 extraction windows (each proposing a new age for the character, a
fact, a relationship event and a mood) and all 29 summary steps (24 chunks, 5 rollups) leave the SHA-256 of every row of
`characters`, `character_traits`, `character_visual_profiles` and `character_versions` unchanged, with every proposed
age stored as an `identity_candidate` only. Its memory tests are listed under [Memory](#memory).

Delivered in P04.8a: `backend/tests/unit/test_reply_normalizer.py` holds T-CON-09. Six fixed raw replies, two per
template family and written in the habits of its profiles (Markdown, labels with a colon, a dash or bold, asterisk
narration, wrapping quotes, a table, emoji sequences, two cut by length), run under the rules of the three demo cards.
Each gives its expected text and rule counts in one piece, cut in two at every position, and cut into equal chunks of
1 to 8 characters. The text released while streaming, followed by the tail, equals the delivered text, except where N4
cut it. The same file covers each rule alone, what is held back while streaming, and the rules read from a card.
`backend/tests/integration/test_reply_streaming.py` adds a reply whose label, narration span and bold markers never
reach a delta or the database, whose N4 cut reaches `message.completed`, and whose variant records
`{"normalizer": {"N1": 1, "N2": 2, "N3": 1, "N4": 1}}`. In this change `scripts/lyrveil.cmd test unit
tests/unit/test_reply_normalizer.py` (41 passed), the same command with `tests/integration/test_reply_streaming.py`
(7 passed), and `scripts/lyrveil.cmd test lint` passed; the full levels were not run.

Delivered in P04.8b: `backend/tests/unit/test_reply_checks.py` holds T-CON-08 on fixed texts. V2 and V3 are tested
through the cut of each template family: control text, a frame marker, the user's turn, and a reply that opens with it,
which leaves the empty text of V1. Each is tested in one piece and split at every chunk size. V4 blocks a minor next to
a sexual term, quoted or not, and lets a child alone pass. V5 passes when the user asked. V6 covers its negation, the
character's own names, `Lei` and a quoted name. V7 covers the first three words, a near copy and the 10-reply window.
V8 covers English in an Italian chat, the reverse and a short reply. V9 covers the opening and repetition rules and `mi
fa senso`. V10 covers digits and Italian words, durations, a quotation and English. V11 skips quotations, also across
sentences, with typographic quotes and with an unpaired quote. It also skips `solo` as an adverb, plurals, invariable
adjectives, `non_indicato` and other languages. V12 and V13 run on both address forms, with `tè`. The same file covers
the sentence splitter at every chunk size, the first stop, the rules read from a card, the adaptive-note rules (start,
3 replies, two at most, V7 twice), the record parser and the note texts. `backend/tests/integration/test_reply_streaming.py`
holds T-CON-10. A V6, V10 or V11 hit stops the stream (the runtime gets the abort of the job's genkey), publishes
`message.restarted` with the check, and generates once more with one more copy of the note in the prompt. The new
deltas start at offset 0, and the variant records the restart. When the corrective generation hits again, its text is
delivered and its hit recorded. Seven replies in one chat show the V9, V8 and V13 notes starting, lasting 3 replies,
and the oldest giving way to keep two. The file also covers V4 (the notice, `CONTENT_POLICY_BLOCKED`, the event order)
and V1 (one retry from the same prompt without a restart event, then `REPLY_EMPTY`). In this change
`scripts/lyrveil.cmd test unit tests/unit/test_reply_checks.py` (63 passed), the same command with
`tests/integration/test_reply_streaming.py` (14 passed, the 7 earlier tests included, one with the new `checks_json`
shape), the same two with the content guard, prompt formatter, normalizer, context builder, state and import-rule files
(266 passed), and `scripts/lyrveil.cmd test lint` passed; the full levels were not run.

### Messaging

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-MSG-01 | Create a chat. Delivered: `backend/tests/integration/test_conversations.py` and `test_characters.py`, and the first test of `tests/e2e/specs/chat-list.spec.mjs` (minimal character form, new chat, reload) | Integration, E2E | P02 |
| T-MSG-02 | Send is idempotent, and is committed before the job exists | Integration | P02 |
| T-MSG-03 | Streaming: order, persistence every 2 s, final text. Delivered: the first test of `tests/e2e/specs/conversation-stream.spec.mjs` (a reply from the fake KoboldCpp, stored, same after a reload), `reply-stream.spec.mjs` for the pieces and offsets, and `conversation-store.spec.ts` | Integration, E2E | P02 |
| T-MSG-04 | Regenerate adds variants, and alternatives can be selected. Delivered: the second test of `conversation-stream.spec.mjs` (**Rigenera**, `2/2`, stepping, the choice after a reload) | Integration, E2E | P02 |
| T-MSG-05 | Edit. Delivered: the second test of `tests/e2e/specs/conversation.spec.mjs` | Integration, E2E | P02 |
| T-MSG-06 | Delete. Delivered: the same test | Integration, E2E | P02 |
| T-MSG-07 | Page reload during streaming resumes correctly. Delivered: the third test of `conversation-stream.spec.mjs` holds back the frames after the first piece of a reply, reloads, and expects the whole reply once; the fake's reply takes about a second, so a reply that is half stored is covered by the store test "resumes a reply after a page reload" | E2E | P02 |
| T-MSG-08 | History paging and recovery. Delivered: the fourth test of `tests/e2e/specs/conversation.spec.mjs` | Integration, E2E | P02 |
| T-MSG-09 | Simultaneous conversations are served in order, with no mixed text. Delivered on the screen side: the fourth test of `conversation-stream.spec.mjs` (two chats, each with its own reply only) | Integration, E2E | P02 |
| T-MSG-10 | Read state and unread counts. Written in P07.6 and run in the [P07 exit](verification/2026-11-09-p07-exit.md): `backend/tests/integration/test_read_state.py` (the count, the read route, the preview and activity of a row, the derived read state), the "read mark" block of `conversation-store.spec.ts`, the live-rows test of `chats-store.spec.ts` and `tests/e2e/specs/read-state.spec.mjs` | Integration, E2E | P07 |
| T-MSG-11 | Pin, archive, delete, restore, search. Delivered: `test_conversations.py`, `test_messages.py` and the second test of `tests/e2e/specs/chat-list.spec.mjs` | Integration, E2E | P02 |
| T-MSG-12 | WebSocket reconnect with replay and resync. Delivered: the fifth test of `conversation-stream.spec.mjs` cuts the connection, sends a message and expects the reply after the page reconnects; the store test "reloads through REST when the server asks for a resync" covers the resync | Integration, E2E | P02 |
| T-MSG-13 | Crash during streaming leaves `interrupted` | Integration | P02 |

The browser tests of the conversation screen use the Compose test project, where the orchestrator grants a lease and the
fake KoboldCpp streams `Ciao, sono qui.` for every reply. That reply takes about a second, so `conversation-stream.spec.mjs`
uses Playwright's WebSocket routing to hold frames back (T-MSG-07) or cut the connection (T-MSG-12) while the real
backend and orchestrator do the rest. Not covered in a browser: a reply that is half stored when the page reloads, and
the server order of simultaneous jobs (the backend and orchestrator tests cover the server side).

### Images

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-IMG-01 | Prompt composition is deterministic. Safety negative always present. In `backend/tests/unit/test_visual_consistency.py` (P05.3b) | Unit | P05 |
| T-IMG-02 | Scene filter: the sanitizer, then tags over 4 words, unknown tags in vocabulary lists, duplicates, blocked terms, identity categories and locked categories, in the documented order. In `backend/tests/unit/test_visual_consistency.py` (P05.3b) | Unit | P05 |
| T-IMG-03 | Request detector on at least 100 phrases: 59 positive and 57 negative Italian phrases, the distance limit, the clause rule for negations and the language lookup. In `backend/tests/unit/test_image_request_detector.py` (P05.5a). The integration part, in `backend/tests/integration/test_image_modes.py`, sends the phrases through `MessageService.send` and the message route and checks the job kind, the `intent` values, the settings switch and the replay of a `client_message_id` | Unit, Integration | P05 |
| T-IMG-04 | GPU alternation in the right order, with fakes. In `orchestrator/tests/integration/test_image_lifecycle.py` (P06.1): the 18 steps of [an image request](gpu-orchestration.md#the-18-steps-of-an-image-request) through the control API, step by step, with the documented state of each step, the stop and start calls in order with their grace periods, the verified releases and the job id on the switch's events; a second test adds a lost Docker after step 18, so every state of the table is reached. The label key of every state is checked in `backend/tests/unit/test_gpu_state.py`, its Italian text in `frontend/src/app/core/i18n/translator.spec.ts`, and the `gpu.state` frame on the socket in `backend/tests/integration/test_reply_streaming.py` | Integration | P06 |
| T-IMG-05 | ComfyUI messages map to progress | Unit | P05 |
| T-IMG-06 | Persistence is atomic and idempotent. In `backend/tests/integration/test_image_job.py` (P05.4b): the `persist` stage run twice leaves one row, one original and one thumbnail, and a staged file that is not an image leaves no row and no file and fails the message | Integration | P05 |
| T-IMG-07 | The image appears as a message with metadata. The integration part is in `test_image_job.py` (P05.4b), through the scheduler with a fake text model and a fake ComfyUI; the browser part is the first test of `tests/e2e/specs/image-request.spec.mjs` (P05.5b): the form sends the request to the real backend, the placeholder shows the queue note, and `image.progress` frames for the prompt, first pass, second pass and saving, then `message.completed`, are delivered through Playwright's WebSocket routing, which since P05.7 also drops the real worker's image frames, so each step is checkable; it passed in the P05 exit on 2026-11-07 after two fixes in the spec itself ([evidence](verification/2026-11-07-p05-exit.md)). The store side is in `conversation-store.spec.ts` ("image requests") | Integration, E2E | P05 |
| T-IMG-08 | Images load with ComfyUI off. The integration part is in `test_image_job.py` (P05.4b): the media routes answer after the fake ComfyUI is switched off; the browser part is still open: the conversation screen shows the thumbnail from `/media/images/{id}/thumbnail` and `image-request.spec.mjs` checks only that address, because its image id is scripted | Integration, E2E | P05 |
| T-IMG-09 | Regeneration adds a variant. In `test_image_job.py` (P05.4b): a new variant with a new seed (or the same with `keep_seed`) and no text-model call, the older variant keeps its image | Integration | P05 |
| T-IMG-10 | Cancellation at each stage. In `backend/tests/integration/test_job_cancellation.py` (P06.3): queued in `prompt`, `render` and `persist` (cancelled at once, placeholder and staged file gone, nothing stored), running `prompt` (generation aborted with its `genkey`) and `render` (`POST /interrupt`), running `persist` refused, a running reply that keeps its text as `interrupted`, and `POST /jobs/{id}/cancel` with its `404` and `409` | Integration | P06 |
| T-IMG-11 | Out of memory leads to a tiled retry of the same preset, then one **Rapida** try. The stored preset is the one that produced the file. In `backend/tests/integration/test_image_oom.py` (P06.4), through the scheduler with a fake ComfyUI that runs out of memory: **Alta**, tiled **Alta**, then tiled **Rapida**, each after an `oom` release, with the same seed, stored as `rapida` with `sdxl_base_tiled.v2.json`; a failed **Rapida** try that is not retried; and a **Rapida** request with its tiled retry only | Integration | P06 |
| T-IMG-12 | Restart in each stage resumes without duplicates. In `backend/tests/integration/test_image_restart.py` (P06.5): the backend process dies in `prompt` (scene generation), in `render` (after its first progress message), and in `persist` before and after its commit; a second process runs the startup recovery and the remaining stages, and the chat holds one image message with its caption, one `generated_images` row, one original, one thumbnail and no staged file, with the seed and preset of the request | Integration | P06 |
| T-IMG-13 | Contextual mode off by default. A proposal needs a valid event and acceptance. In `backend/tests/integration/test_image_modes.py` (P05.5a): with the setting off no job is queued and no proposal stored; a decision is stored only when it says `propose`, has a scene that passes the content guard and names a live message of an active chat, and then `image.proposed` is published; only an acceptance queues an `image_request` (once), a rejection queues nothing, and an answer after 24 hours is refused. The model step that writes the decision is not covered: it does not exist yet | Integration | P05 |
| T-IMG-14 | **Alta** and **Rapida** build the documented graphs: node classes, links, shared seed, final sizes, `ckpt_name` from the profile only, tiled variants. On real hardware, render time and peak VRAM per preset are recorded and the preset rule applied. The unit part is `backend/tests/unit/test_workflows.py` (P05.2b). The real-GPU part is `tests/gpu/ImageProfiles.Tests.ps1` (GPU.5), with the rule in `backend/tests/gpu_probe/image_presets.py`; on the reference machine on 2026-11-25 **Alta** took 21.06 s at the 95th percentile for every profile, so it stays the default ([evidence](verification/2026-11-25-gpu-image-profiles.md)) | Unit, real GPU | P05 |
| T-IMG-15 | The identity segment of the positive prompt is byte-identical for scenes written by every template family, and follows the fixed order, age band and caps. In `backend/tests/unit/test_visual_consistency.py`, with one scene per template family written by hand (P05.3b) | Unit | P05 |
| T-IMG-16 | Sanitizer: weights, escapes, `embedding:`, brackets and commas in tags are neutralized. Every vocabulary entry passes it unchanged, and its token count matches ComfyUI's tokenizer. The advanced dialog refuses `embedding:` and weights outside 0.5 to 1.5. The unit part is `backend/tests/unit/test_tag_vocabulary.py`, the tokenizer match is in `tests/docker/ImageRuntime.Tests.ps1` (P05.3a) | Unit, Docker | P05 |
| T-IMG-17 | `ImageScene` grammar limits `outfit` to the wardrobe, and drops `scene` when `clothing` is locked. `auto` maps each framing to its size, and **Alta** sizes are multiples of 8. Invalid output leads to the fallback scene. In `backend/tests/unit/test_image_scene.py` (P05.4a) | Unit | P05 |
| T-IMG-18 | The caption is written through the chat path before the scene, with the normalizer and checks. A failed caption sends the image without text. In `backend/tests/integration/test_image_caption.py`, on the prompt stage with a fake runtime (P05.4a) | Integration | P05 |

### Models

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-MOD-01 | `config/model-catalog.json` passes validation. Invalid identifiers, moving revisions, unknown templates or presets, invalid `swa_padding`, `smart_cache` or `quality_evidence`, and two defaults for one tier are refused. Custom profiles get the `generic` preset | Unit | P02 |
| T-MOD-02 | Each `PromptFormatter` family produces the golden string rendered from the official template for the same turns. No family writes BOS | Unit | P02 |
| T-MOD-03 | A text profile change writes the launch file, stops the runtime, verifies release and loads the new file. A profile above the tier or with a missing file is refused before any mode change. Backend side in `backend/tests/integration/test_model_profiles.py` (P06.6a), through the routes and the real scheduler with a fake orchestrator: the change writes every launch key at level A and asks for no mode, **Applica ora** runs ahead of a queued reply, asks for `none`, waits for `IDLE` and only then for `llm`, and the reply's lease comes after the new file loaded; a target profile on the reduced tier, a missing file and an unknown profile are refused with the launch file and the setting unchanged, and a file gone after the job was queued fails it with `MODEL_FILE_MISSING` and no mode request. Orchestrator side in `orchestrator/tests/integration/test_profile_change.py`: `none` waits for the reply's lease, the release is verified before `IDLE`, and `llm` starts the runtime again. Not run against the real runtime | Integration | P06 |
| T-MOD-04 | Custom registration accepts only file names in the two model folders and only `.gguf` and `.safetensors`. Traversal, links out of the folder and other extensions are refused. The hash job stores the profile. In `backend/tests/integration/test_model_profiles.py` (P06.6a): traversals with `/` and `\`, a path, `.ckpt`, `.pt`, `.bin`, the other kind's extension, a link out of the folder and one inside it, a folder, a name with a space, a model folder that is a link, a missing template, a missing file and a catalog file are refused and queue nothing; the hash job stores a text and an image profile under `custom-<12 hex>`, which can be selected, cannot be removed while active, and turns `size_mismatch` after a size change; a file replaced by a link after the request fails the job and stores nothing | Integration | P06 |
| T-MOD-05 | Prompt layout: two consecutive replies share the stable text and the history byte for byte for each family. Old turns never carry the frame. Frame markers in user text, memories and summaries are neutralized. Proactive and structured prompts follow their own layout | Unit | P02 |

### Orchestration

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-ORC-01 | Every legal transition works and every other is refused | Unit | P01 |
| T-ORC-02 | Property: no event sequence runs both runtimes. In `orchestrator/tests/unit/test_exclusivity_property.py` (P06.1): Hypothesis drives the backend's calls and outside faults, also while a container call is in flight, through the real manager and supervisor over the fakes ([Exclusivity rules](gpu-orchestration.md#exclusivity-rules)) | Unit | P06 |
| T-ORC-03 | Lease rules: one at a time, mode match, expiry, heartbeat | Unit | P02 |
| T-ORC-04 | Release verification with external memory use. No external process is touched. In `orchestrator/tests/unit/test_vram_guard.py` (P06.2): another program takes memory as the runtime stops, the release is verified with `drop_shortfall_mib` in its event, and the gate only ever names the two runtime containers | Unit | P06 |
| T-ORC-05 | Low VRAM blocks with a reason and ends the job after the wait. Orchestrator side in `orchestrator/tests/unit/test_vram_guard.py` (P06.2): the hold in `IDLE` with `blocked_reason` and `blocked_vram`, the check every 5 s, the load once memory is free, and the end of the hold when no runtime is wanted. Backend side in `backend/tests/integration/test_image_mode_switch.py` and `test_reply_streaming.py`: the job fails with `GPU_INSUFFICIENT_VRAM` after 120 s, its message becomes `failed`, and the next job waits anew | Unit | P06 |
| T-ORC-06 | Each row of the recovery table. In `orchestrator/tests/unit/test_recovery_table.py` (P06.4): from process start over the fakes, each row with the stops it makes and the state it ends in, a runtime found starting that turns healthy or not, the timed retry of `DOCKER_UNAVAILABLE` and its limit of three an hour, no timed retry for a missing container, and the restart after an `oom` release | Unit | P06 |
| T-ORC-07 | Gate refuses other names and foreign labels. No forbidden call exists | Unit | P01 |
| T-ORC-08 | Fallback profiles: the launch levels after an out of memory. Orchestrator side in `orchestrator/tests/unit/test_recovery_table.py` (P06.4): a failed text load classified by exit status 3 or an allocation log line, and not for another failure or a refused launch file, its `load_failure`, three failures to `ERROR`, and three failed image loads back to text. Backend side in `backend/tests/unit/test_launch_levels.py` (the values of A, B and C, the lowering to C and no further, the files left alone, once per failure across a restart) and `backend/tests/integration/test_launch_levels.py`: a reply out of memory lowers the level before its lease is released, the follower lowers it for a classified load failure, and the image job named by a final image failure ends with `IMAGE_LOAD_FAILED`. Not run against the real runtime | Integration | P06 |
| T-ORC-09 | Drain and shutdown. Orchestrator side in `orchestrator/tests/integration/test_drain.py` (P06.5): the drain waits for the current lease, then stops the runtime of either mode with its release verified, refuses new leases and modes and loads nothing; resume applies the mode asked before; a shutdown leaves the containers, and a new process keeps the healthy runtime without the old lease, or, after a drain, loads text again. Backend side in `backend/tests/integration/test_shutdown_drain.py`: the queue drain lets the running job finish and starts no other, also through `JobQueue.start`, a paused queue asks for no mode, a drain out of time followed by the shutdown aborts the generation and releases the lease, the next start requeues the job and resumes the queue, and the `drain`, `drain-gpu` and `resume` commands report each outcome. `stop.cmd` runs both drain steps in T-DCK-02 and T-DCK-03, which check that the stop succeeds, not the drain rows. Not run against the real runtimes or GPU | Integration | P06 |
| T-ORC-10 | Control API refuses a missing or wrong token. Since P02.6c `tests/docker/Orchestrator.Tests.ps1` also checks it on the production image | Integration, Docker | P01 |
| T-ORC-11 | Events replay without duplicates after a backend restart. In `backend/tests/integration/test_lifecycle_events.py` (P06.5), with a fake event buffer that answers as `GET /v1/events`: a restarted backend on the same file resumes after the newest stored event, an overlapping answer stores nothing twice, a new orchestrator process is read from its first event while the old rows stay, events no longer buffered are skipped, an orchestrator that does not answer loses nothing, the newest rows are kept, and the job worker's follower stores the events across two application lifetimes | Integration | P06 |
| T-ORC-12 | Scheduling rules: priorities, batching, fairness, preemption. The rules in `backend/tests/unit/test_scheduling_rules.py` (P06.3), with plain values for the queue, the batch and the idle time; the preemption through the scheduler, with the runtime's abort and the attempt given back, in `backend/tests/integration/test_job_cancellation.py`; the batch and idle clocks through the mode switch in `test_image_mode_switch.py` | Unit | P06 |
| T-ORC-13 | A text message during image work is saved, queued, shown and answered. Integration part in `backend/tests/integration/test_image_mode_switch.py` (P06.3): sent while the render holds the image lease, the reply is stored `queued` and first in line, waits for the image, takes the GPU back and is answered by the real reply handler. Screen part in `conversation-store.spec.ts` ("says a reply queued behind image work…") and the T-ORC-13 test of `tests/e2e/specs/image-request.spec.mjs`, both written and not run in P06.3 | Integration, E2E | P06 |

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

Tests of the in-app notifications (P07.5) have no catalog identifier of their own (`test_notifications.py` and `notifications.spec.mjs`); they ran with the levels of the [P07 exit](verification/2026-11-09-p07-exit.md).
`backend/tests/integration/test_notifications.py` covers the three routes, the cursor, the unread count and the event on
the hub; `test_proactive_messages.py` and `test_missed_events.py` check that the delivery and the startup step announce
their notice; `frontend/src/app/features/notifications/notifications-store.spec.ts` covers the store; and
`tests/e2e/specs/notifications.spec.mjs` shows the badge, the list, the toast and marking read. In that last file the
browser plays the notification routes and the event, because nothing switches proactive messages on yet.

### Security

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-SEC-01 | Wrong Host and wrong Origin are refused on REST and WebSocket. Delivered: `backend/tests/integration/test_local_only.py`, in process with Starlette's test client | Integration | P01 |
| T-SEC-02 | Headers present on every response. Delivered: `tests/e2e/specs/security-headers.spec.mjs` checks the shell, a client route, a hashed and a missing asset, an API answer, an API 404 and a refused write through nginx, each header exactly once and no CORS header; `test_local_only.py` checks the backend's own headers | E2E | P01 |
| T-SEC-03 | Markup in messages renders as text. Delivered: the first test of `tests/e2e/specs/conversation.spec.mjs` | E2E | P02 |
| T-SEC-04 | Path traversal attempts on media and asset routes. Delivered: `test_avatars.py` sends encoded `..`, `\` and absolute paths to `/media/assets/...` and to the asset routes, and a row with a tampered path is neither served nor deleted | Integration | P03 |
| T-SEC-05 | Decompression bomb and disguised uploads are refused. Delivered: `test_avatars.py`, with PNG headers of 20000 x 20000, 9000 x 9000 and 9000 x 10 pixels, HTML, SVG, GIF, broken PNG and JPEG bytes, and types that disagree with the bytes | Integration | P03 |
| T-SEC-06 | Marker strings never reach logs | Integration | P02 |
| T-SEC-07 | Content guard cases, including minors at each entry point. Delivered in P03.6a, and passed in the targeted runs named below the Character Creator table: `backend/tests/unit/test_content_guard.py` (each rule in Italian and English, the childhood phrases, durations and near words that pass, case, accents, invisible characters, spaced letters and digit swaps, every text field of a card, the draft description, every list entry written normalized, and that no setting, environment variable or file can change the minor safeguards), `backend/tests/integration/test_content_guard.py` (create in both body forms, save, proposal, restore and duplicate of a card stored past the guard, import, the age `CHECK` on insert and update, and the same refusals with non-default settings, hostile environment variables and `application_settings` rows) and the draft tests of `test_character_drafts.py` | Unit, integration | P03 |
| T-SEC-08 | Delete refuses paths outside the media root. Delivered: `backend/tests/unit/test_media_store.py`: `..`, absolute paths, the root, files directly in it, the staging folder and a link that leads outside | Unit | P03 |
| T-SEC-09 | Lint rule forbids `innerHTML` and `bypassSecurityTrust`. Delivered: `frontend/tools/eslint/unsafe-html.test.mjs` lints fixtures with the real configuration | Unit | P01 |

### Docker

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-DCK-01 | `start.cmd` from a clean clone | Docker | P01 |
| T-DCK-02 | `stop.cmd`, also when already stopped | Docker | P01 |
| T-DCK-03 | `restart.cmd` | Docker | P01 |
| T-DCK-04 | Data survives container recreation and upgrades | Docker | P01 |
| T-DCK-05 | Health checks reach healthy, and report failures | Docker | P01 |
| T-DCK-06 | Scripts run twice with the same result | Docker | P01 |
| T-DCK-07 | Preflight failures print the right advice | Docker | P01 |
| T-DCK-08 | Backend and runtimes have no route to the Internet | Docker | P01 |
| T-DCK-09 | Only `127.0.0.1:4200` is published | Docker | P01 |
| T-DCK-10 | Images contain no data, weights, `.env` or fakes | Docker | P01 |
| T-DCK-11 | Model download resumes, verifies and skips valid files, with a local test server | Docker | P01 |
| T-DCK-12 | Backup and restore round trip, including the copy to the Windows backup folder | Docker | P03 |
| T-DCK-13 | Volumes are external and survive `docker compose down -v`. Root `.cmd` files only delegate. PSScriptAnalyzer passes | Docker | P01 |
| T-DCK-14 | `import-model` copies a valid file and refuses other extensions, invalid names and existing names. The three `import-model` tests of `tests/docker/Data.Tests.ps1` (P06.6a); not run in this version yet | Docker | P06 |

### Real GPU

Executed only on real hardware, with the results recorded in `docs/verification/`.

| ID | Test | Phase |
| --- | --- | --- |
| T-GPU-01 | GPU passthrough in `lyrveil-llm` and `lyrveil-image`. `tests/gpu/Passthrough.Tests.ps1` in the [`gpu` level](#the-gpu-level) (GPU.1); passed on the reference machine on 2026-11-25 ([evidence](verification/2026-11-25-gpu-level.md)) | P02, P05 |
| T-GPU-02 | The catalog text profiles load: `rocinante-x-12b` and `rocinante-12b-v1.1` since [ADR 0045](decisions/0045-drop-gemma-4-rocinante-x-default.md). VRAM and RAM measured, tuning rule applied. `tests/gpu/TextProfiles.Tests.ps1` in the [`gpu` level](#the-gpu-level) (GPU.2), with 3 generations for the peak instead of 20 and without steps 5 to 9 of the procedure; passed on the reference machine on 2026-11-25 for `gemma-4-26b-a4b`, `gemma-4-12b` and `rocinante-x-12b` at their earlier catalog values ([evidence](verification/2026-11-25-gpu-text-profiles.md)); for both Rocinante profiles at 12288 the results file of 2026-11-25 records no problem and about 2210 MiB of margin, with the Pester summary of that run not seen ([evidence](verification/2026-11-25-rocinante-default-gpu.md)) | P02 |
| T-GPU-03 | Tokenization fixture recorded for each template family. `tests/gpu/TextProfiles.Tests.ps1` (GPU.2), fixtures in `backend/tests/fixtures/tokenization/`; passed on the reference machine on 2026-11-25 ([evidence](verification/2026-11-25-gpu-text-profiles.md)) | P02 |
| T-GPU-04 | Real streaming, token counts, abort. `tests/gpu/TextProfiles.Tests.ps1` (GPU.2), for every family; passed on the reference machine on 2026-11-25 ([evidence](verification/2026-11-25-gpu-text-profiles.md)) | P02 |
| T-GPU-05 | Structured output with grammar on the real model. `tests/gpu/StructuredOutput.Tests.ps1` (GPU.4), the `character_draft` task on the default text profile; passed on the reference machine on 2026-11-25 ([evidence](verification/2026-11-25-gpu-structured-output.md)) | P03 |
| T-GPU-06 | ComfyUI start, real generation with each image profile, checkpoint load measured. `tests/gpu/ImageProfiles.Tests.ps1` (GPU.5), for every catalog image profile; passed on the reference machine on 2026-11-25 ([evidence](verification/2026-11-25-gpu-image-profiles.md)) | P05 |
| T-GPU-07 | Ten full switches. Container state and NVML samples never show both models. `tests/gpu/ModeSwitch.Tests.ps1` in the [`gpu` level](#the-gpu-level) (GPU.6), through the test-only orchestrator; ten round trips passed on the reference machine on 2026-11-25 in two runs of five, each full switch 41.7 to 43.7 s ([evidence](verification/2026-11-25-gpu-mode-switches.md)) | P06 |
| T-GPU-08 | Text sent during real generation is answered after the switch. `tests/gpu/ModeSwitch.Tests.ps1` (GPU.6), with the real backend; passed on the reference machine on 2026-11-25 ([evidence](verification/2026-11-25-gpu-mode-switches.md)) | P06 |
| T-GPU-09 | External VRAM use: blocked with reason, no process touched. `tests/gpu/ModeSwitch.Tests.ps1` (GPU.6), with a VRAM load the test starts and removes itself; passed on the reference machine on 2026-11-25 ([evidence](verification/2026-11-25-gpu-mode-switches.md)) | P06 |
| T-GPU-10 | Real CUDA out-of-memory handled, by reducing available VRAM on purpose. `tests/gpu/Resilience.Tests.ps1` (GPU.7), run alone with the item `t-gpu-10`; **pending GPU.7b**: on Windows the driver's shared-memory fallback means no allocation fails on the reference machine, so its cases are reported as skipped in every other run ([evidence](verification/2026-11-26-gpu-resilience.md)) | P06 |
| T-GPU-11 | No orphan process and full VRAM release after `stop.cmd`. `tests/gpu/Resilience.Tests.ps1` in the [`gpu` level](#the-gpu-level) (GPU.7), the code of `stop.cmd` on the test project; passed on the reference machine on 2026-11-26 ([evidence](verification/2026-11-26-gpu-resilience.md)) | P06 |
| T-GPU-12 | Everything works with the network disconnected. `tests/gpu/Resilience.Tests.ps1` (GPU.7): the backend, the orchestrator and both runtimes on internal networks only, probed for routes, connections and lookups, answering and rendering on the real GPU; passed on the reference machine on 2026-11-26. The frontend, the browser and a start with the host's network disconnected still need a manual unplug ([evidence](verification/2026-11-26-gpu-resilience.md)) | P06 |
| T-GPU-13 | Two-hour session with 20 images and 3 conversations. `tests/gpu/Session.Tests.ps1` (GPU.8), opt-in: `scripts\lyrveil.cmd test gpu Session` runs the two hours and `... Session short` the same driver for 150 s; the driver is written and its short mode passed once on the reference machine on 2026-11-26; the two-hour run has not been made | P08 |
| T-GPU-14 | [Speed rule](gpu-orchestration.md#measuring-and-tuning) for the default text profile, comparing AutoFit with `MOE_CPU_LAYERS` for a mixture-of-experts default, and the [efficiency levers](decisions/0030-koboldcpp-efficiency-levers.md). `tests/gpu/Tuning.Tests.ps1` in the [`gpu` level](#the-gpu-level) (GPU.3), with steps 1 to 9; since [ADR 0045](decisions/0045-drop-gemma-4-rocinante-x-default.md) for `rocinante-x-12b`, the dense default. Passed on the reference machine on 2026-11-25 over twelve partial runs for the earlier default `gemma-4-26b-a4b`: AutoFit at 42.6 T/s and a first token in 9.04 s ([evidence](verification/2026-11-25-gpu-tuning.md)); passed again the same day for `rocinante-x-12b` at 12288 over three partial runs of procedure version 2: 53.0 T/s and a first token in 2.22 s ([evidence](verification/2026-11-25-rocinante-default-gpu.md)) | P02 |

### Quality

| ID | Test | Phase |
| --- | --- | --- |
| T-QLT-01 | [Italian quality protocol](prompting.md#italian-quality-protocol) | P04, P08 |
| T-QLT-02 | [Image default grid](image-generation.md#default-parameters) for each image profile, with the owner's blind review | P05 |
| T-QLT-03 | Accessibility: `axe` on ten screens, keyboard walkthrough | P08 |
| T-QLT-04 | Responsive checks at 320, 640, 1024 and 1440 px, and 200 % zoom | P08 |
| T-QLT-05 | [Consistency protocol](consistency-engine.md#consistency-protocol) on every catalog text profile, with the SWA padding and SmartCache measurements | P04, P08 |
| T-QLT-06 | [Extraction evaluation](consistency-engine.md#extraction-evaluation) on every catalog text profile | P04 |
| T-QLT-07 | [Image identity protocol](image-generation.md#image-identity-protocol), with the [measured levers](image-generation.md#measured-levers) | P05 |

### Character cards

Planned for version 1.1 ([ADR 0047](decisions/0047-character-card-import-and-export.md)). T-CRD-01 and the unit part
of T-CRD-04 are written, in `backend/tests/unit/test_tavern_card.py` (P10.1), and T-CRD-02 is written in
`backend/tests/integration/test_card_imports.py` (P10.2a). T-CRD-03, the age part of T-CRD-04 and T-CRD-06 are written
in `backend/tests/unit/test_card_mapping.py` (P10.2b). That file also covers T-MOD-05 with an imported card. T-CRD-05
and the conversion-model part of T-CRD-04 are written in `backend/tests/integration/test_card_conversion.py` (P10.3),
which also checks the delimited card text in the prompt, and the downgrade of revision `0019` is in
`backend/tests/integration/test_migrations.py`. The API part of T-CRD-04 (a missing or under-18 age refused when
saving an import) is written in `backend/tests/integration/test_card_save.py` (P10.4), with the avatar, the scenarios
from greetings and the one-save rule. T-CRD-07 is written in `backend/tests/integration/test_card_export.py` (P10.5a):
the V3 and V2 envelopes, the required fields, both PNG chunks, the native card and its hash, the source fields and
unknown `extensions` keys of an imported character, and the error for `png` without an avatar. T-CRD-08 is written in `backend/tests/integration/test_card_roundtrip.py`
(P10.5b): Lyrveil, then a V3 PNG, then Lyrveil gives an equal card; an edited card names its changed fields; and a
tampered embedded card with a matching hash is refused. Its last clause, a card then Lyrveil then a card keeping every
unknown field, is checked in `test_card_export.py`. T-CRD-09 is written in `tests/e2e/specs/card-import.spec.mjs`
(P10.6): a V2 PNG card is imported, the preview is read, the age is entered, the character is saved with the scenarios
of its greetings, and it is exported as PNG, in the native format and as the original card; a second test checks that a
refused file shows its error. The E2E spec was not run when it was written. The browser's own parts are unit specs:
`card-import.model.spec.ts`, `card-import-preview.spec.ts`, `card-actions.spec.ts` and
`character-creator-import.spec.ts`. The structured conversion needs the text model, so the E2E does not run it; the unit
spec follows it with a mocked job. The other tests are not written yet. The
fixtures are original synthetic cards written for the tests. No community card is committed
([R-PUB-06](requirements.md#17-publication)). The V1, V2 and V3 JSON cards are in `backend/tests/fixtures/tavern_cards`.
The PNG and APNG cards are built from them, chunk by chunk, by the test module.

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-CRD-01 | Parsing: V1, V2 and V3 JSON, and PNG and APNG with `chara`, `ccv3` or both (`ccv3` wins). Refused: a bad chunk length or CRC, bad base64, bad JSON, nesting over 64 levels, a file over 64 MB, an unknown `spec`, a CHARX archive (`415`) | Unit | P10 |
| T-CRD-02 | An import stores the parsed card unchanged with the file hash, `GET .../source` returns it, any refusal stores nothing, and an unsaved import expires with its staged avatar | Integration | P10 |
| T-CRD-03 | Direct mapping as in the table: traits by paragraph, examples from `<START>` blocks, pairs over the limits left out and reported, macros replaced and listed, greetings turned into scenarios only when asked | Unit | P10 |
| T-CRD-04 | Age: empty without an explicit adult age, filled with one, the conversion model's age never used. A minor in any string, the lorebook included, answers `422 CONTENT_POLICY_BLOCKED` with the source path | Unit, integration | P10 |
| T-CRD-05 | `card_conversion` with the fake runtime: a valid draft, a repaired one, two refusals, and `CARD_TOO_LONG` before any generation | Integration | P10 |
| T-CRD-06 | `{{char}}` and `{{user}}` render at prompt time with the first name and the user's name. `system_prompt`, `post_history_instructions` and `creator_notes` never reach a reply, draft, extraction or proactive prompt | Unit | P10 |
| T-CRD-07 | Export as V3 JSON, V2 JSON and PNG: envelope values, every required field, both PNG chunks, `extensions.lyrveil`, the kept source fields and unknown `extensions` keys, and no conversation, memory, persona or user name | Unit, integration | P10 |
| T-CRD-08 | Round trips: Lyrveil, then a V3 PNG, then Lyrveil gives an equal card; a card edited elsewhere names its changed fields; a card, then Lyrveil, then a card keeps every unknown field | Integration | P10 |
| T-CRD-09 | In the browser: import a PNG card, read the preview, enter the age, save with scenarios, export as PNG | E2E | P10 |

### Personas

Planned for version 1.1 ([ADR 0048](decisions/0048-user-personas.md)). T-PSN-01 and the database half of T-PSN-02
(lengths, age, one default, the default never archived, the delete rule and the fixed `persona_id`) are written in
`backend/tests/integration/test_migrations.py` (P11.1a) and were not run when they were written. The API half of
T-PSN-02 (name, age, gender, the content guard on every text and on the note, the default, the archive and delete
rules) and the conversation half of T-PSN-04 (`persona_id` fixed, `persona_note` editable) are written in
`backend/tests/integration/test_personas.py` (P11.1b), together with the choice of persona for a new conversation; they
were not run when they were written. T-PSN-03 and the prompt half of T-PSN-04 are written in P11.2: the unit half in
`backend/tests/unit/test_context_builder.py` (the persona in blocks 1 and 3, the anchor and the frame, block 10 after
block 3, its 300-token cap, the `persona` reduction after `identity`, and T-MOD-05 with block 10), and the integration
half in `backend/tests/integration/test_persona_prompts.py` (a reply through the scheduler with its stop sequences and
check V12, the extraction, summary and proactive prompts, and a persona and note edit seen by the next prompt only,
with a second persona whose texts never appear). They were not run when they were written. T-PSN-05 is written in
`backend/tests/integration/test_memory_retrieval.py` (P11.3) and T-PSN-06 in `tests/e2e/specs/personas.spec.mjs`
(P11.4: a second persona, a chat with it and a note, both in the context panel, the note edited there, and an archived
persona hidden from the picker), with `personas-store.spec.ts` for the store. None of these tests ran when they were
written; all of them passed in the P11 exit run, with T-MOD-05 and block 10
([evidence](verification/2026-11-27-p11-exit.md)). AC-23 in a manual run is pending with P11.EXIT-HW.

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-PSN-01 | The migration creates the default persona from the two settings, or from their defaults without a row, sets it on every conversation and shared memory, and removes the keys | Integration | P11 |
| T-PSN-02 | Validation: name length, age NULL or 18 to 120 at the API and in the database, the content guard on every text, one default, and the archive and delete rules | Unit, integration | P11 |
| T-PSN-03 | The conversation's persona reaches block 1, block 3, block 10, the anchor, the stop sequences, the reply checks and the extraction and proactive prompts. No text of another persona reaches any prompt | Unit, integration | P11 |
| T-PSN-04 | `persona_id` cannot change after creation, the note can, and a persona edit changes block 10 from the next reply only | Integration | P11 |
| T-PSN-05 | With `shares_memory`, a conversation reads the shared memories of its own persona and never those of another | Integration | P11 |
| T-PSN-06 | In the browser: create a second persona, start a chat with it and a note, see both in the context panel | E2E | P11 |

### Lore

Planned for version 1.1 ([ADR 0049](decisions/0049-worlds-and-lore-entries.md)). T-LOR-04 is written in P12.1a; the
P12.1a session could not start a test run, so its first run is the `unit` and `integration` levels of the orchestrator.
T-LOR-01 to T-LOR-03 are written in P12.2a and P12.2b, T-LOR-06 in P12.3, and T-LOR-05 and T-LOR-08 in P12.4, as
below. T-LOR-07 is written in P12.5b.

- **T-LOR-04.** `tests/unit/test_lore_rules.py` holds the limits, the content guard on titles, contents and world names,
  the keys with no stem (`lui`), the stored key text with its stems and the block-maximum flag.
  `tests/integration/test_lore.py` holds the same through `LoreService` on a migrated database, the 500-entry limit
  and an entry over its maximum stored with `never_inserted`. `tests/integration/test_migrations.py` holds the `CHECK`s of
  the three tables, one own book per character, the 500-entry trigger and the links.
  `tests/integration/test_lore_api.py` (P12.1b, written, not run in that session) holds the API half: the status codes of
  the lore routes, an invalid entry refused with its field and limit, and the own book that `DELETE` refuses.
- **T-LOR-01.** `tests/unit/test_lore_matcher.py` (P12.2a) holds the matcher: stem sequences in Italian and English
  (contiguous, in order, never across two messages), exact case-sensitive keys at word boundaries, secondary and exclude
  keys, disabled and constant entries, the scan depth, keys saved in another language stemmed again, and the key `lui`
  refused on save. The unit file ran with 15 passed. `tests/integration/test_lore_api.py` holds the route
  `POST /lore/books/{id}/match`; it was written and not run in that session, because the integration level takes no
  test filter.
- **T-LOR-02, link part.** The same file holds the links: exactly one target, only a world is linked, and a conversation
  started from a scenario copies the scenario's links, so a later change of the scenario's links leaves it unchanged.
- **T-LOR-02, reply part.** `tests/integration/test_lore_prompts.py` (P12.2b) builds the prompts of three chats of one
  character as a reply loads them: each reads the own book and the world linked to the character, the first chat reads
  its own linked world once more without a duplicate and its activated entry in block 12, the second chat reads only its
  own linked world, the chat started from a scenario reads the copy of the scenario's link, and an unlinked world reaches
  no prompt.
- **T-LOR-03.** `tests/unit/test_lore_prompt.py` (P12.2b) holds the order (priority, own book first, insertion order),
  the walk within the block and book budgets, an entry over its maximum, block 11 after block 10 in the stable text and
  block 12 after block 5 in the frame with the placeholders rendered, the targets of 300 and 500, the reduction order
  (`lore_activated` before the memories, `lore_constant` after the persona) and, for every template family, a prefix
  that stays byte-identical over twelve replies while the activated entry changes with each message.
- **P12.2b run.** The two files above passed in targeted runs in the P12.2b session (11 unit tests and 1 integration
  test), and so did the unit tests of the builder, the formatter, the matcher and the tokenization fixtures, T-MOD-05
  included, with version 2 of block 1, and the integration files `test_persona_prompts.py`, `test_context_builder.py`,
  `test_image_caption.py`, `test_character_state.py`, `test_lore.py` and `test_lore_api.py` (38 passed). The full
  `unit` and `integration` levels did not run in that session.
- **T-LOR-06.** `tests/integration/test_lore_diagnostics.py` (P12.3) holds three tests. A reply on a chat with a
  constant entry in the own book and a world whose book budget of 50 tokens keeps one keyed entry and cuts the other
  is read through `GET /messages/{id}/diagnostics`: `lore_refs` lists the three entries apart from the (empty)
  `memories`, the kept ones first, each with its book, the SHA-256 of its content, its matched key and its block, the
  cut one as `cut` in `lore_activated`, and the stored prompt text holds the kept entries and not the cut one. A reply
  with no lore has `lore_refs` equal to `[]`. A memory extraction on a chat with a constant and a keyed entry never
  sends either to the runtime. `tests/integration/test_migrations.py` checks that migration 0022 adds the column and
  that the downgrade drops it. The frontend spec `memory-studio-screen.spec.ts` checks **Voci di lore**. In the P12.3
  session the new file passed (3 tests), and so did `test_migrations.py` and `test_diagnostics.py` (69); the frontend
  spec and the full levels did not run.
- **T-LOR-07.** `tests/e2e/specs/lore.spec.mjs` (P12.5b, written, not run in that session) creates a world in the
  browser, adds an entry, links the world to two characters from its editor, uses **Prova le parole chiave** (the entry
  is active for a text with its key and inactive for another), has a reply to a message with the key read the entry and
  finds it under **Voci di lore** in diagnostic mode, then removes one link from the character's profile and checks that
  the next reply's `lore_refs` is empty while the world and the other link stay. `test_lore_api.py` holds the route that
  lists a target's links, and the frontend specs `keyword-test.spec.ts`, `world-links.spec.ts` and `lore.model.spec.ts`
  hold the keyword test, the link list and the wording of each reason. The keyword test needs no model because the
  route runs the matcher alone; the E2E stack has the fake runtimes, so it does not prove that no model is loaded.
- **T-LOR-05.** `tests/unit/test_lorebook.py` (P12.4) holds the converter: the field mapping, the four applied
  decorators and the kept ones with their `@@@` fallbacks, `secondary_keys` only with `selective`, the `use_regex` and
  keyless entries disabled and reported, the unknown fields in `source_json`, the entries left out for their content
  and the book values out of range. `tests/integration/test_lorebook.py` saves a card import whose `character_book`
  has one entry per rule and reads the own book, its entries and their kept sources back, checks `report.lore`, and
  checks that the V3 and V2 card exports carry the book as `character_book` and that a character with an empty own
  book exports none. `tests/integration/test_migrations.py` checks that migration 0023 converts a source stored at
  revision 0022, skips a card without a book, and converts nothing more on a second run.
- **T-LOR-08.** `tests/unit/test_lorebook.py` and `tests/integration/test_lorebook.py` import a `lorebook_v3` file
  with kept decorators, a regex entry, a keyless entry, unapplied secondary keys, an out-of-range `token_budget` and
  unknown book and entry fields, export it, and find every field of the file again; a second import and export gives
  the same file. A wrong `spec`, a field of the wrong type and a minor in the content are refused, and nothing is
  stored. In the P12.4 session the two new files passed (13 tests), and so did `test_card_export.py` (T-CRD-07),
  `test_card_roundtrip.py` (T-CRD-08), `test_card_save.py`, `test_card_imports.py` and `test_migrations.py`, 119 in
  all, and `test_lore.py`, `test_lore_api.py` and `test_lore_rules.py`; the full levels did not run.

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-LOR-01 | Matcher: stem sequences in Italian and English, exact case-sensitive keys, secondary and excluded keys, disabled and constant entries, the scan depth, and keys refused for having no stem | Unit | P12 |
| T-LOR-02 | A reply reads the own book, the character's worlds and the conversation's worlds. A scenario's links are copied at the start, and no link of another conversation applies | Integration | P12 |
| T-LOR-03 | Selection and placement: priority, own book first, insertion order, block and book budgets, the new reduction order, block 11 in the stable text and block 12 in the dynamic context. The prefix stays byte-identical over twelve replies while the activated entries change | Unit | P12 |
| T-LOR-04 | Validation: the field limits, the content guard on titles, contents and names, the 500-entry limit, and an entry over its block's maximum stored and never inserted | Unit, integration | P12 |
| T-LOR-05 | A card's `character_book` becomes the own book. The decorators are applied or kept, `use_regex` entries are disabled and reported, unknown fields are kept, and the migration converts the sources stored by P10 | Integration | P12 |
| T-LOR-06 | Each assembly stores `lore_refs`, diagnostic mode shows them apart from the memories, and extraction never reads lore | Integration | P12 |
| T-LOR-07 | In the browser: create a world, add entries, link it to two characters, use **Prova le parole chiave**, and see an entry under **Voci di lore** | E2E | P12 |
| T-LOR-08 | `lorebook_v3` import and export: a round trip keeps every field, and the decorators that were not applied come back as lines | Unit, integration | P12 |

## Acceptance

P09 walks through [AC-01 to AC-20](requirements.md#21-acceptance-criteria) on a clean clone with the real hardware. It records each result in `docs/verification/`.

P10 to P12 add [AC-21 to AC-24](requirements.md#acceptance-criteria-for-version-11) for version 1.1. Each phase records its criteria in a manual run, and the 1.1 release runs AC-01 to AC-24 again on a clean clone.

## Evidence

`docs/verification/YYYY-MM-DD-<topic>.md` holds what ran: the exact command, the versions, the result counts, and what was not run. Use real numbers. A failure stays recorded after it is fixed, together with the fix. Roadmap items link to these files.

## Continuous integration

`.github/workflows/ci.yml` (P01.8d) runs on every push. **It is written and has not run:** no push has triggered it, and the commands below have not been observed on a hosted runner. Hosted runners have no GPU, so CI never runs `gpu` or `quality` and never claims them passed. Linux containers need a Linux runner, so every job runs on `ubuntu-latest` except `scripts`, which runs PowerShell on `windows-latest` with Windows PowerShell 5.1, because a hosted Windows runner cannot start Linux containers. `scripts\lyrveil.cmd` itself is Windows-only and is not called in CI: each job runs the commands that the matching local level runs.

| Job | Local level it matches | Commands |
| --- | --- | --- |
| `scripts` (Windows) | `lint`: documentation checks and PSScriptAnalyzer. `unit`: Pester | `Invoke-ScriptLint` of [`TestCommand.psm1`](../scripts/lib/TestCommand.psm1), the same function `lint` calls first, then `Invoke-PesterUnitTest` on `tests/scripts/*.Tests.ps1` |
| `python` (backend, orchestrator) | `lint`, `unit`, `integration`: the Python part | `docker build --target test`, then in it, with `--network none`: `ruff check .`, `ruff format --check .`, `mypy --strict src tests`, `pytest tests/unit`, `pytest tests/integration` |
| `frontend` | `lint`, `unit`: the frontend part | In `frontend/` with Node.js 24: `npm ci --ignore-scripts`, `npm run lint`, `npm run build`, `npm run test` |
| `e2e` | `e2e` | `npx playwright install --with-deps chromium`, `docker compose -p lyrveil-test -f compose.test.yaml up --build --detach --wait` for the frontend, the orchestrator over its fakes and the two fake runtimes, `npx playwright test`, then `down --volumes --remove-orphans` whatever happened |
| `images` (frontend, backend, orchestrator) | Image builds of `docker` | `docker build --target runtime` of each Dockerfile, the target `compose.yaml` builds. Only the build is checked here; the Compose project is not started |
| `gitleaks` | None: the secret scan has no local level | gitleaks 8.30.1, downloaded from its release and checked against the release's checksum file, runs `gitleaks git --log-opts=<before>..<after>` on the commits of the push, or on the whole history of the pushed commit when the push creates a branch or the earlier commit is gone |
| `mermaid` | None | `node --test scripts/ci/*.test.mjs`, then [`scripts/ci/check-mermaid.mjs`](../scripts/ci/check-mermaid.mjs), which extracts every diagram from the tracked Markdown and renders each with `@mermaid-js/mermaid-cli` 11.12.0 in the runner's Chrome; any syntax error, or no diagram found, fails the job |

The `docker` level is not in CI: its Pester files drive `docker.cmd`, `cmd.exe` and the Windows ACLs of `setup.cmd`, and need a Windows host with Linux containers. The documentation links, anchors, requirement coverage and test catalog checks run in `scripts`, as part of `lint`.

Limits, stated so that a green run is not read as more than it is:

- **The contract test ran once, locally, in the P01 exit, and passed** after a fix to its fixture: `from_asgi` takes no `base_url` in 4.33.0, so the `Host` goes in `headers` ([evidence](verification/2026-11-07-p01-exit.md#failure-found-and-fixed)). The `python` CI job has not run it. Schemathesis 4.33.0 is in the `dev` group of `backend/pyproject.toml`, and `backend/uv.lock` was regenerated with the `lockfile` stage of `backend/Dockerfile` (the test image built from it). [`backend/tests/integration/test_contract.py`](../backend/tests/integration/test_contract.py) generates requests for every operation of the application's own OpenAPI document and validates each answer, with the allowed `Host` set. It runs in `pytest tests/integration`, so both the `integration` level and the `python` job run it. `mypy --strict` accepts it (observed in `scripts\lyrveil.cmd test lint`), with the library's result and case types typed `Any` in the test. Only the two health routes exist, and the WebSocket route is not in an OpenAPI document.
- The Mermaid command line is installed with `npm install` at an exact version, but without a lock file, so its dependencies are not pinned by hash. The GitHub Actions are referenced by major version tag, not by commit.
- The `gitleaks` archive is verified against the checksum file of the same release, which detects a damaged download but not a replaced release. Pin the archive hash in the workflow once it has been recorded on a first run.
