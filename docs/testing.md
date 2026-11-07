# Testing

Status: accepted (2026-11-01), revised 2026-11-06 for the consistency and image identity tests, the delivered `lint` level and the backend gates, and 2026-11-06 for the `docker` level. **Only the catalog tests named in this paragraph have run.** The `e2e` level exists (P01.8c): its smoke test passed against the fakes ([evidence](verification/2026-11-06-e2e-level.md)). The fake runtimes of `tests/fakes/` and `compose.test.yaml` are written (P01.8b) ([evidence](verification/2026-11-06-fake-runtimes.md)); their Docker test, `tests/docker/TestFakes.Tests.ps1`, first ran and passed with P01.6a, after two fixes to the test itself. The CI workflow `.github/workflows/ci.yml` is written (P01.8d) and has not run; see [Continuous integration](#continuous-integration). Every other catalog test is planned and assigned to a roadmap phase. Delivered so far: the `test` command with its `lint` level, the Pester tests of the scripts in the `unit` level ([evidence](verification/2026-11-06-lint.md)), and the backend's ruff, format and `mypy --strict` gates in `lint` and its pytest unit tests in `unit`, all in the backend test stage ([evidence](verification/2026-11-06-backend-skeleton.md)). The backend part of the `integration` level (database, migrations and health routes) is delivered too ([evidence](verification/2026-11-06-backend-persistence.md)). The orchestrator's gates and unit tests, with T-ORC-01, run in its own test stage ([evidence](verification/2026-11-06-orchestrator-state-machine.md)), and so does T-ORC-07, with the scan that confines the Docker socket to the container gate. The backend's unit tests run the same scan on the backend ([evidence](verification/2026-11-06-container-gate.md)). The orchestrator's integration tests, with T-ORC-10, run in the `integration` level, and its unit tests cover each threshold of the GPU preflight with a fake probe ([evidence](verification/2026-11-06-control-api.md)). The frontend's ESLint, color check and strict build run in `lint`, and its Vitest and Node tests, with T-SEC-09, run in `unit` ([evidence](verification/2026-11-06-frontend-shell.md)). The `docker` level ran in full and passed, with T-DCK-06, T-DCK-07 and T-DCK-13 for `setup.cmd` ([evidence](verification/2026-11-06-setup.md)).

## Levels

| Level | Tool | Runs on | GPU |
| --- | --- | --- | --- |
| Lint | Documentation checks, PSScriptAnalyzer, ruff and `mypy --strict` for the backend and the orchestrator, and ESLint, the color check, the generated API types check and the strict `ng build` for the frontend | Any Windows machine with Git, Docker and Node.js 24 | No |
| Unit | pytest, Hypothesis, Vitest, Pester for the scripts | Any machine | No |
| Integration | pytest with SQLite and fake runtimes | Any machine | No |
| Contract | Schemathesis on OpenAPI (the backend's, in `backend/tests/integration/test_contract.py`, run by the `integration` level and the `python` CI job); recorded runtime fixtures are planned | Any machine | No |
| End-to-end | Playwright with `compose.test.yaml` and fakes | Any machine with Docker | No |
| Docker | Pester and pytest against the real Compose project | Docker | Partly |
| GPU | pytest with real runtimes and models | Reference machine | Yes |
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

Every other level refuses arguments.

`all` runs `lint`, `unit`, `integration`, `e2e` and `docker`. It leaves out `gpu` and `quality`, which need the hardware and are explicit. `gpu` exits with a clear skip message when no GPU is present. A skip is never reported as a pass.

`integration` builds the test stage of each component that has a `tests/integration` folder, today the backend and the orchestrator, and runs `pytest tests/integration` in it, without network access. The backend's tests use SQLite files in the container's own file system. The test stage has the Python and SQLite build of the runtime image, so WAL and FTS5 are proven on the build that production uses. The tests start the engine, the Alembic command line, the health routes and `main()` against real database files, with Uvicorn replaced by a stand-in. The orchestrator's tests run the control API in process through Starlette's test client, with a fake GPU probe.

Every command exits 0 only when the level ran tests and all of them passed. An unknown level, a missing level and a level with no tests yet exit 1 with a message. `docker` runs the Pester files `tests/docker/*.Tests.ps1` with the host's Docker, and fails when there are none. It builds the images of `compose.yaml` and of `tests/fakes/Dockerfile`, so it needs Docker and takes a while on a first run. See [The `docker` level](#the-docker-level). Today `gpu` and `quality` have no tests, so they exit 1.

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
  with the ready line. The round trip of T-DCK-12 is completed in P03.7b and T-DCK-14 in P06.
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

The setup tests never touch a real installation. They add `tests/docker/compose.setup-test.yaml` to `compose.yaml`,
which renames the two external volumes to `lyrveil-setup-test-data` and `lyrveil-setup-test-models`, tags every image
`:setup-test`, and adds a fake `lyrveil-llm` so setup has a runtime container to create. Every command names the
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
network and mount the Windows folder read-only, how `LYRVEIL_BACKUP_DIR` is resolved, and that wrong arguments,
extensions and names are refused before any Docker call.

### The `e2e` level

Delivered in P01.8c ([evidence](verification/2026-11-06-e2e-level.md)). [`scripts/lib/EndToEnd.psm1`](../scripts/lib/EndToEnd.psm1) runs it from Windows with Node.js 24 and Docker:

1. It runs `npm ci --ignore-scripts` in `tests/e2e/` when `node_modules` is missing or older than the lock, then `npx playwright install chromium`, which downloads the browser of the locked Playwright 1.64.1 into the user's Playwright cache.
2. It removes any earlier project named `lyrveil-test`, so the volumes start empty, then runs `docker compose -p lyrveil-test -f compose.test.yaml up --build --detach --wait` for `lyrveil-frontend` (which starts `lyrveil-backend`), `lyrveil-llm` and `lyrveil-image`. The orchestrator is left out until P02, because it exits at once and `up --wait` fails on an exited service.
3. It runs `npx playwright test` against `http://127.0.0.1:14200`, or the port in `LYRVEIL_TEST_HTTP_PORT`. It refuses 4200.
4. In a `finally` block, whatever happened before, it runs `docker compose -p lyrveil-test ... down --volumes --remove-orphans`, then lists the containers and volumes that still carry the project's label. The level fails if any is left or if the removal cannot be checked.

The level touches only the project `lyrveil-test`, named with `-p` so no `COMPOSE_PROJECT_NAME` can redirect it, and never `lyrveil-data`, `lyrveil-models` or the backup folder. The smoke test, `tests/e2e/specs/smoke.spec.mjs`, opens the empty app, checks the redirect to the chat list, the title and the heading, and fails on any [axe-core](https://github.com/dequelabs/axe-core) violation. `tests/e2e/specs/security-headers.spec.mjs` is T-SEC-02. `compose.test.yaml` gives the backend `LYRVEIL_HTTP_PORT` equal to the test port, because the backend refuses any other port in `Host` and `Origin`. The Pester cases "The e2e level" in `tests/scripts/TestCommand.Tests.ps1` check the order of the steps and the cleanup with stand-in commands; they ran and passed in the `unit` level of the P01 exit ([evidence](verification/2026-11-07-p01-exit.md)).

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
  quality/italian/
  quality/consistency/
  quality/extraction/
  quality/images/
```

### Fake runtimes

Delivered in P01.8b. Their tests run in the orchestrator's test stage with the `unit` level ([evidence](verification/2026-11-06-fake-runtimes.md)).

- `fake_kobold` and `fake_comfy` are HTTP servers on the standard library that implement exactly the contracts in [Runtime contracts](runtime-contracts.md): KoboldCpp's eight endpoints, and ComfyUI's five HTTP endpoints and its `/ws` WebSocket. A test reads the endpoint tables of that page and compares them with the routes of each fake, and checks that any other path answers 404 and another method 405. They support scripted replies, delays, invalid JSON, out-of-memory errors and crashes.
- A test scripts a behavior with `fake.script("POST /api/v1/generate", Step(action="oom"))`. Each `Step` is used once, by the next request to that route. The container reads the same steps from the `FAKE_SCRIPT` environment variable as JSON. A fake has no control endpoint, so it serves nothing that the contract does not list.
- `fake_comfy` plays a job on the WebSocket of its `client_id` with the messages of the contract. The steps scripted on `WS /ws` shape the next job: an out-of-memory error, an unparsable frame, a crash after N messages or delays. `POST /interrupt` ends the running job with `execution_interrupted`.
- The out-of-memory reply of `fake_kobold`, a 500 answer naming CUDA and a stream that ends with `finish_reason` "error", is an assumption: the contract does not record how KoboldCpp reports it.
- `fake_docker` and `fake_nvml` implement `ContainerGate` and `GpuProbe`. `FakeContainerGate` subclasses the real gate without opening the socket, keeps the state of both containers, refuses a missing or foreign container and a name outside the allowlist, and raises queued failures with `fail`. `FakeGpuProbe` models a card shared with other programs: external memory use, scripted between reads, queued and permanent failures, and a count of reads.
- They live only in `tests/fakes/` and in `compose.test.yaml`, and never enter a production image. `tests/docker/TestFakes.Tests.ps1` lists the file system of each production image and fails if `fakes` or a fake's name appears. It also lists the image built from `tests/fakes/Dockerfile`, to show that the check can find them. Only the orchestrator's test stage and that image copy `tests/fakes/`.
- `compose.test.yaml` starts the three core services with the two HTTP fakes as the services `lyrveil-llm` and `lyrveil-image`. It has its own project name `lyrveil-test`, its own port, its own empty volumes, its own `:test` image tags and inline settings, and no `container_name`. The same Docker test reads the resolved configuration and fails if it names an external volume, a host folder, the Docker socket, `gpus` or port 4200.
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
| T-CHR-14 | Voice and wardrobe fields: `grammatical_gender` with its CHECK, `address_form`, `narration`, `emoji`, example exchanges, `avoid`, identity slots against the vocabulary, outfits with reserved keys refused. Round trip through export and import | Unit, integration | P03 |

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
| T-CON-08 | Reply checks V1 to V13 on fixed texts, including quotations that V11 must skip | Unit | P04 |
| T-CON-09 | Reply normalizer N1 to N5 on fixed raw replies from each template family, in one piece and split into stream chunks at every position. The delivered text is identical in both cases | Unit | P04 |
| T-CON-10 | A V6, V10 or V11 hit aborts the stream, publishes `message.restarted` and regenerates once with the note in the anchor. A second hit is delivered and recorded. Adaptive notes start, last 3 replies and stay at most two | Integration | P04 |
| T-CON-11 | Length bands set `max_length` and the reply reserve, capped by `llm.max_length`. The anchor holds the band's text. Block 3 renders examples in saved order and stops before the 500-token maximum | Unit | P04 |

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
| T-IMG-02 | Scene filter: the sanitizer, then tags over 4 words, unknown tags in vocabulary lists, duplicates, blocked terms, identity categories and locked categories, in the documented order | Unit | P05 |
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
| T-IMG-15 | The identity segment of the positive prompt is byte-identical for scenes written by every template family, and follows the fixed order, age band and caps | Unit | P05 |
| T-IMG-16 | Sanitizer: weights, escapes, `embedding:`, brackets and commas in tags are neutralized. Every vocabulary entry passes it unchanged, and its token count matches ComfyUI's tokenizer. The advanced dialog refuses `embedding:` and weights outside 0.5 to 1.5 | Unit | P05 |
| T-IMG-17 | `ImageScene` grammar limits `outfit` to the wardrobe, and drops `scene` when `clothing` is locked. `auto` maps each framing to its size, and **Alta** sizes are multiples of 8. Invalid output leads to the fallback scene | Unit | P05 |
| T-IMG-18 | The caption is written through the chat path before the scene, with the normalizer and checks. A failed caption sends the image without text | Integration | P05 |

### Models

| ID | Test | Level | Phase |
| --- | --- | --- | --- |
| T-MOD-01 | `config/model-catalog.json` passes validation. Invalid identifiers, moving revisions, unknown templates or presets, invalid `swa_padding`, `smart_cache` or `quality_evidence`, and two defaults for one tier are refused. Custom profiles get the `generic` preset | Unit | P02 |
| T-MOD-02 | Each `PromptFormatter` family produces the golden string rendered from the official template for the same turns. No family writes BOS | Unit | P02 |
| T-MOD-03 | A text profile change writes the launch file, stops the runtime, verifies release and loads the new file. A profile above the tier or with a missing file is refused before any mode change | Integration | P06 |
| T-MOD-04 | Custom registration accepts only file names in the two model folders and only `.gguf` and `.safetensors`. Traversal, links out of the folder and other extensions are refused. The hash job stores the profile | Integration | P06 |
| T-MOD-05 | Prompt layout: two consecutive replies share the stable text and the history byte for byte for each family. Old turns never carry the frame. Frame markers in user text, memories and summaries are neutralized. Proactive and structured prompts follow their own layout | Unit | P02 |

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
| T-SEC-01 | Wrong Host and wrong Origin are refused on REST and WebSocket. Delivered: `backend/tests/integration/test_local_only.py`, in process with Starlette's test client | Integration | P01 |
| T-SEC-02 | Headers present on every response. Delivered: `tests/e2e/specs/security-headers.spec.mjs` checks the shell, a client route, a hashed and a missing asset, an API answer, an API 404 and a refused write through nginx, each header exactly once and no CORS header; `test_local_only.py` checks the backend's own headers | E2E | P01 |
| T-SEC-03 | Markup in messages renders as text | E2E | P02 |
| T-SEC-04 | Path traversal attempts on media and asset routes | Integration | P03 |
| T-SEC-05 | Decompression bomb and disguised uploads are refused | Integration | P03 |
| T-SEC-06 | Marker strings never reach logs | Integration | P02 |
| T-SEC-07 | Content guard cases, including minors at each entry point | Unit | P03 |
| T-SEC-08 | Delete refuses paths outside the media root | Unit | P03 |
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
| T-DCK-14 | `import-model` copies a valid file and refuses other extensions, invalid names and existing names | Docker | P06 |

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
| T-GPU-11 | No orphan process and full VRAM release after `stop.cmd` | P06 |
| T-GPU-12 | Everything works with the network disconnected | P06 |
| T-GPU-13 | Two-hour session with 20 images and 3 conversations | P08 |
| T-GPU-14 | [Speed rule](gpu-orchestration.md#measuring-and-tuning) for the default text profile, comparing AutoFit with `MOE_CPU_LAYERS`, and the [efficiency levers](decisions/0030-koboldcpp-efficiency-levers.md) | P02 |

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

## Acceptance

P09 walks through [AC-01 to AC-20](requirements.md#21-acceptance-criteria) on a clean clone with the real hardware. It records each result in `docs/verification/`.

## Evidence

`docs/verification/YYYY-MM-DD-<topic>.md` holds what ran: the exact command, the versions, the result counts, and what was not run. Use real numbers. A failure stays recorded after it is fixed, together with the fix. Roadmap items link to these files.

## Continuous integration

`.github/workflows/ci.yml` (P01.8d) runs on every push. **It is written and has not run:** no push has triggered it, and the commands below have not been observed on a hosted runner. Hosted runners have no GPU, so CI never runs `gpu` or `quality` and never claims them passed. Linux containers need a Linux runner, so every job runs on `ubuntu-latest` except `scripts`, which runs PowerShell on `windows-latest` with Windows PowerShell 5.1, because a hosted Windows runner cannot start Linux containers. `scripts\lyrveil.cmd` itself is Windows-only and is not called in CI: each job runs the commands that the matching local level runs.

| Job | Local level it matches | Commands |
| --- | --- | --- |
| `scripts` (Windows) | `lint`: documentation checks and PSScriptAnalyzer. `unit`: Pester | `Invoke-ScriptLint` of [`TestCommand.psm1`](../scripts/lib/TestCommand.psm1), the same function `lint` calls first, then `Invoke-PesterUnitTest` on `tests/scripts/*.Tests.ps1` |
| `python` (backend, orchestrator) | `lint`, `unit`, `integration`: the Python part | `docker build --target test`, then in it, with `--network none`: `ruff check .`, `ruff format --check .`, `mypy --strict src tests`, `pytest tests/unit`, `pytest tests/integration` |
| `frontend` | `lint`, `unit`: the frontend part | In `frontend/` with Node.js 24: `npm ci --ignore-scripts`, `npm run lint`, `npm run build`, `npm run test` |
| `e2e` | `e2e` | `npx playwright install --with-deps chromium`, `docker compose -p lyrveil-test -f compose.test.yaml up --build --detach --wait` for the frontend and the two fakes, `npx playwright test`, then `down --volumes --remove-orphans` whatever happened |
| `images` (frontend, backend, orchestrator) | Image builds of `docker` | `docker build --target runtime` of each Dockerfile, the target `compose.yaml` builds. Only the build is checked here; the Compose project is not started |
| `gitleaks` | None: the secret scan has no local level | gitleaks 8.30.1, downloaded from its release and checked against the release's checksum file, runs `gitleaks git --log-opts=<before>..<after>` on the commits of the push, or on the whole history of the pushed commit when the push creates a branch or the earlier commit is gone |
| `mermaid` | None | `node --test scripts/ci/*.test.mjs`, then [`scripts/ci/check-mermaid.mjs`](../scripts/ci/check-mermaid.mjs), which extracts every diagram from the tracked Markdown and renders each with `@mermaid-js/mermaid-cli` 11.12.0 in the runner's Chrome; any syntax error, or no diagram found, fails the job |

The `docker` level is not in CI: its Pester files drive `docker.cmd`, `cmd.exe` and the Windows ACLs of `setup.cmd`, and need a Windows host with Linux containers. The documentation links, anchors, requirement coverage and test catalog checks run in `scripts`, as part of `lint`.

Limits, stated so that a green run is not read as more than it is:

- **The contract test ran once, locally, in the P01 exit, and passed** after a fix to its fixture: `from_asgi` takes no `base_url` in 4.33.0, so the `Host` goes in `headers` ([evidence](verification/2026-11-07-p01-exit.md#failure-found-and-fixed)). The `python` CI job has not run it. Schemathesis 4.33.0 is in the `dev` group of `backend/pyproject.toml`, and `backend/uv.lock` was regenerated with the `lockfile` stage of `backend/Dockerfile` (the test image built from it). [`backend/tests/integration/test_contract.py`](../backend/tests/integration/test_contract.py) generates requests for every operation of the application's own OpenAPI document and validates each answer, with the allowed `Host` set. It runs in `pytest tests/integration`, so both the `integration` level and the `python` job run it. `mypy --strict` accepts it (observed in `scripts\lyrveil.cmd test lint`), with the library's result and case types typed `Any` in the test. Only the two health routes exist, and the WebSocket route is not in an OpenAPI document.
- The Mermaid command line is installed with `npm install` at an exact version, but without a lock file, so its dependencies are not pinned by hash. The GitHub Actions are referenced by major version tag, not by commit.
- The `gitleaks` archive is verified against the checksum file of the same release, which detects a damaged download but not a replaced release. Pin the archive hash in the workflow once it has been recorded on a first run.
