# Operations

Status: accepted (2026-11-01), revised 2026-11-05 for model profiles, the 32 GB reference machine and operation from
Windows ([ADR 0029](decisions/0029-windows-operation-with-docker-volumes.md)). Most scripts and files described here do not exist yet. They are delivered in phase P01 and completed through P06. This guide is their specification; [Reference Compose file](#reference-compose-file) says what `compose.yaml` already holds.

## Requirements

| Item | Target | Reduced tier | Not supported |
| --- | --- | --- | --- |
| Windows | Windows 11 | Windows 11 | Other systems are untested |
| GPU | RTX 4070, 12 GB | Same | Under 10000 MiB of VRAM |
| Windows RAM | 32 GB | 16 GB | Under 16 GB |
| WSL2 `memory` | `24GB` | `12GB` | Docker sees under 11 GiB |
| WSL2 `swap` | `8GB` | `16GB` | |
| Free disk | 50 GB on the drive that holds Docker Desktop's data, usually `C:` | 40 GB | |
| Software | Docker Desktop with WSL2 backend, Git for Windows. No Linux distribution is needed | Same | |
| NVIDIA driver | CUDA driver version at least 13.0, Windows driver only | Same | A Linux NVIDIA driver inside WSL2 |

In the reduced tier, close other heavy programs during image generation. See [ADR 0027](decisions/0027-hardware-tiers-after-ram-upgrade.md).
Each tier has its own default text profile ([Model catalog](models.md#defaults-and-how-they-can-change)).

### Disk use

| Item | Size |
| --- | --- |
| Default text model, target tier: `gemma-4-26B_q4_0-it.gguf` | 14.44 GB |
| Default text model, reduced tier: `gemma-4-12b-it-qat-q4_0.gguf` | 6.98 GB |
| Default image model: `Illustrious-XL-v2.0.safetensors` | 6.94 GB |
| Each optional profile | Its size in the [catalog](models.md) |
| KoboldCpp image | About 1.9 GB (1.89 GB measured on 2026-11-07) |
| ComfyUI image with PyTorch and CUDA | About 8 to 12 GB |
| Frontend, backend and orchestrator images | Under 1 GB |
| Application data | Grows with use. About 2 to 3 MB per **Rapida** image and 4 to 6 MB per **Alta** image |
| Exported backups | One file per day in the Windows backup folder, never pruned by Lyrveil |

Images and volumes live in Docker Desktop's disk image under `%LOCALAPPDATA%\Docker\wsl`. Space freed inside it, for
example by deleting a model, may not return to Windows until Docker Desktop shrinks the image.

## First installation

Everything runs from Windows. Docker Desktop runs the containers in its own WSL2 virtual machine, which you never open.

### 1. Prepare Docker Desktop

Install Docker Desktop with the WSL2 backend, then update WSL from PowerShell:

```powershell
wsl --update
```

### WSL2 memory

The WSL2 default gives Docker half of the Windows memory. Create `%UserProfile%\.wslconfig` with the values for your tier.

For 32 GB:

```ini
[wsl2]
memory=24GB
swap=8GB
```

For 16 GB:

```ini
[wsl2]
memory=12GB
swap=16GB
```

Then apply it from PowerShell, and start Docker Desktop again. This stops all running containers:

```powershell
wsl --shutdown
```

Lyrveil's scripts never change this file. Preflight prints the right values when memory is too low.

### 2. Clone

Any folder on a local drive works. Live data never lives in this folder, so its speed does not matter
([ADR 0029](decisions/0029-windows-operation-with-docker-volumes.md)).

```powershell
git clone https://github.com/AjimuZ/Lyrveil.git $env:USERPROFILE\Desktop\projects\lyrveil
```

```powershell
cd $env:USERPROFILE\Desktop\projects\lyrveil
```

### 3. Run setup

```powershell
.\setup.cmd
```

A double click on `setup.cmd` in Explorer works too.

### 4. Start

```powershell
.\start.cmd
```

Open `http://localhost:4200`.

## Scripts

The root holds six one-line entry points: `setup.cmd`, `start.cmd`, `stop.cmd`, `restart.cmd`, `status.cmd` and
`logs.cmd`. Each runs `scripts\lyrveil.ps1` with Windows PowerShell 5.1, `-NoProfile -ExecutionPolicy Bypass`, and
passes its arguments. The bypass applies to that process only. `scripts\lyrveil.cmd <command>` runs the other commands:
`models`, `import-model`, `backup`, `restore` and `test`.

Delivered so far: `scripts\lyrveil.cmd` and `scripts\lyrveil.ps1` with the `test` command ([Testing](testing.md#commands)),
the six root entry points, `setup.cmd` with the parts listed under [`setup.cmd`](#setupcmd), and the lifecycle commands
in `scripts/lib/Lifecycle.psm1` with the parts listed under each of them below, `models` in
`scripts/lib/Models.psm1` ([Model downloads](#model-downloads)), and `import-model`, `backup` and `restore` in
`scripts/lib/DataFiles.psm1` (P01.6d, [`import-model`](#scriptslyrveilcmd-import-model),
[Backup and restore](#backup-and-restore)). An unknown command exits 1 with a message, and so does an unknown argument.

Questions go through `Read-Confirmation` in `scripts/lib/ConsolePrompt.psm1`. When standard input is not the console,
or PowerShell runs with `-NonInteractive`, it prints the question and answers no at once, so an unattended run never
proceeds by default and the command exits 5. Setup asks no question yet; `models` asks for license acceptance and before
`-Remove`, and `restore` asks before it stops Lyrveil.

All scripts set `$ErrorActionPreference = 'Stop'` and `Set-StrictMode -Version Latest`, check `$LASTEXITCODE` after
every native command, are idempotent, and never delete persistent data.

| Exit status | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Unexpected failure |
| 2 | Preflight failed |
| 3 | Health check timed out |
| 4 | Model file missing or invalid |
| 5 | The user declined |

### `setup.cmd`

Options: `-AcceptModelLicenses`, `-VerifyModels`, `-Rebuild`, `-SkipModels`.

1. Check Git, Docker, Compose, the memory Docker sees, and free space on the drive that holds Docker Desktop's data.
2. Create `.env` if missing, with a new token, and limit its access to the current Windows user with `icacls`.
3. Create the volumes `lyrveil-data` and `lyrveil-models` if missing, with the label `io.lyrveil.volume`. Set their
   owner to UID and GID 10001 once, with a container that has no network and only `CAP_CHOWN`.
4. Build images: `docker compose --profile ai --profile tools build`. The Docker CLI reads the build context directly
   from the Windows folder.
5. Pick the default text profile from the memory Docker sees ([ADR 0027](decisions/0027-hardware-tiers-after-ram-upgrade.md)).
   Show the licenses of the default profiles and ask for acceptance. See [Licensing](licensing.md#models).
6. Download and verify the default profiles, as in [Model downloads](#model-downloads).
7. Write default launch files into the data volume if missing, with the default text profile.
8. Create the runtime containers without starting them: `docker compose --profile ai create lyrveil-llm lyrveil-image`.

Running it again repeats only what is missing.

Delivered in P01.6a, in `scripts/lib/Setup.psm1` ([evidence](verification/2026-11-06-setup.md)):

- Step 1 runs the six rows that setup judges in [Preflight checks](#preflight-checks). Any failure prints its advice and
  exits 2 before anything is changed.
- Step 2 copies `.env.example` to `.env` with a new token of 32 random bytes in hex. The file is written under a
  temporary name, `icacls` removes the inherited entries and grants only the current user's SID, then it is renamed.
  An existing `.env` is never read or changed.
- Step 3 creates the volumes. A volume that exists without the label stops setup with exit status 2 and is left
  untouched, because setup did not create it.
- Step 4 builds only when an image of the Compose file is missing, or always with `-Rebuild`.
- The owner of step 3 is set after step 4, because the one-off containers use the local backend image and never pull.
  A first container with no capability reads the owner of each volume root. Only when it is not `10001:10001`, a
  second container with no network, a read-only root and only `CAP_CHOWN` changes it. Files inside are never changed.
- Step 8 creates the runtime services that the Compose file defines. `compose.yaml` has none yet (P02, P05), so setup
  says so and creates nothing.
- Steps 5 to 7 are not delivered in setup: the models command does licenses and downloads (P01.6c), but it needs
  `config/model-catalog.json`, which arrives in P02.1a, and launch files come with the catalog (P02). Setup prints that
  nothing was downloaded. `-AcceptModelLicenses` and `-VerifyModels` exit 1 until then and point at
  `scripts\lyrveil.cmd models`, and `-SkipModels` is accepted.

### Model downloads

`scripts\lyrveil.cmd models` runs the one-shot `lyrveil-models` service of the `tools` profile. It is the only
container with a route out, and it mounts only the models volume. URLs, sizes and hashes come from
`config/model-catalog.json`, which is part of the backend image ([Model catalog](models.md#catalog-file)). Text files go
to `llm/` in the volume, image checkpoints to `image/checkpoints/`.

| Option | Downloads |
| --- | --- |
| None | The default text and image profiles of the machine's tier: 21.38 GB on the target tier, 13.91 GB on the reduced tier |
| `-Model <id>` | One profile from the catalog. Repeatable. Asks for license acceptance unless `-AcceptModelLicenses` is given |
| `-All` | Every profile allowed on the machine's tier |
| `-List` | Prints the catalog with sizes, licenses and local state. Downloads nothing |
| `-Verify` | Recomputes the hash of every present file. Downloads nothing |
| `-Remove <id>` | Deletes the file of a profile that is not active. Asks for confirmation |

Behavior, in Python with the standard library only:

1. Show the required space for the chosen files and compare it with free space. Stop if insufficient.
2. Skip a file when `/models/.verified.json` records the same size and modification time and a matching hash.
3. Download to `<file>.part` with HTTP range requests, TLS verification, redirects only to HTTPS, and 5 retries 5 s
   apart. Revisions are pinned, so a resume cannot mix two versions.
4. Check the size, then compute the SHA-256.
5. On a mismatch, keep nothing: delete the `.part` file and exit 4.
6. Move to the final name atomically and update `/models/.verified.json`.

No account or token is needed, because every catalog repository is public and not gated. Downloads are independent of
image builds, and weights never enter an image.

Delivered in P01.6c, in `scripts/lib/Models.psm1` and `backend/src/lyrveil/tools/models.py`
([evidence](verification/2026-11-07-models.md)). `config/model-catalog.json` is not in the image until P02.1a, so on an
installation every option except a refused argument fails with exit status 1 and a message naming the missing catalog.
The behavior below was verified only with a test catalog and a local HTTPS server (T-DCK-11).

- The command checks Docker (exit 2), then that the models volume exists with the label and the backend image is
  present (exit 2, run `setup.cmd`). The tier is `target` when Docker sees at least 20 GiB and `reduced` otherwise,
  also when the memory cannot be read.
- The tool runs with `docker compose --profile tools run --rm --no-deps -T lyrveil-models`, on the network
  `lyrveil-download` only, as `10001:10001` with a read-only root.
- Before any download the command prints each profile still to fetch, with its size, file, license name and URL, and
  the total, then asks once. With `-AcceptModelLicenses` it does not ask. When the answer is no, or nobody can answer,
  it exits 5 and no request is sent. Profiles already verified need no acceptance. The tool itself refuses to download
  without `--accept-licenses`, also with exit status 5.
- A present file of the right size without a record is hashed and kept when it matches. Free space is compared with
  the bytes still missing, `.part` files counted, and too little space exits 2 before any request.
- Only `https://` URLs are opened, with certificate and host name checks; the opener has no handler for other schemes,
  and the tool follows up to 10 redirects itself, each only to HTTPS, keeping the `Range` header. A server that
  ignores the range restarts the file. An HTTP status other than 408, 429 or 5xx, a refused redirect or a certificate
  error fails at once with exit status 1. Any failure deletes the `.part` file; only an interrupted process leaves it,
  and the next run resumes from it.
- `-List` prints every catalog profile with kind, size, state (`missing`, `present, verified`, `present, not verified`
  or `present, wrong size`) and license, and marks the tier's defaults and the profiles above the tier. It reads the
  sizes and `.verified.json` only and hashes nothing.
- `-Verify` hashes every present catalog file and rewrites `.verified.json`. A file that differs loses its record, is
  kept for the user to replace, and makes the command exit 4.
- `-Remove <id>` refuses the active profiles with exit status 1. Until the launch file (P02) and the image setting
  (P05) exist, the active profiles are the defaults of the tier. For any other profile with a file it asks, then
  deletes the file, its `.part` file and its record; no answer exits 5 and deletes nothing.
- `-List`, `-Verify`, `-Remove`, `-Model` and `-All` exclude each other, except that `-Model` can be repeated. A
  conflict, an unknown option or an unknown profile exits 1.

### `scripts\lyrveil.cmd import-model`

`scripts\lyrveil.cmd import-model <file>` copies a model file from Windows into the models volume, for a
[custom profile](models.md#custom-profiles). A `.gguf` goes to `llm/`, a `.safetensors` to `image/checkpoints/`. The
copy runs in a container with no network and the file's folder mounted read-only. Other extensions, names outside
`^[A-Za-z0-9._-]{1,128}\.(gguf|safetensors)$` and names already present are refused. The file is then registered in
**Impostazioni > Modelli**.

Delivered in P01.6d, in `scripts/lib/DataFiles.psm1` and `backend/src/lyrveil/tools/import_model.py`
([evidence](verification/2026-11-07-data-files.md)). Registration in **Impostazioni > Modelli** comes with P06.

- The command takes exactly one file, relative to the current folder. A missing file, another extension (matched with
  case, so `.GGUF` is refused) and a name outside the pattern exit 1 before any Docker call. So does a folder that a
  Docker mount cannot carry: not on a drive, or with a comma or a double quote.
- Docker not running, a models volume that is missing or has no `io.lyrveil.volume` label, and a missing backend image
  exit 2.
- The copy runs `python -m lyrveil.tools.import_model` from the local backend image with `docker run --network none
  --read-only --cap-drop ALL`, as `10001:10001`, with the file's folder mounted read-only at `/import` and the models
  volume at `/models`. The tool imports the standard library only. It refuses a name already in the target folder
  (exit 1), compares the size with the free space (exit 2), copies to a hidden temporary name, then hard-links it to
  the final name, which fails when that name appeared meanwhile, so no file is ever replaced. It prints the size and
  the SHA-256, and leaves nothing behind on a failure.

### `start.cmd`

1. Verify Docker. Exit 2 with instructions if Docker Desktop is not running.
2. Verify Compose at least v2.30, and Engine API at least 1.45, which adds volume subpaths.
3. Verify GPU support with `docker compose run --rm --no-deps lyrveil-orchestrator python -m lyrveil_orchestrator.preflight`, which reads NVML and Docker facts. On a first run the image does not exist yet, so step 5 runs first and this check follows it.
4. Check that both volumes exist and carry the label.
5. Run setup if `.env`, volumes, images or runtime containers are missing.
6. Check the files of the active profiles, presence and exact size, with `docker compose run --rm --no-deps lyrveil-backend python -m lyrveil.cli preflight`. A full hash only runs through `.\setup.cmd -VerifyModels`.
7. Start core services: `docker compose up -d --no-build lyrveil-backend lyrveil-orchestrator lyrveil-frontend`.
8. Wait up to 120 s for health checks. Exit 3 with the last logs on failure.
9. The active text model starts automatically: the orchestrator's default mode is `llm`. Wait up to 300 s for `LLM_READY`. On timeout, print a warning and continue, because the app works for reading and editing.
10. Copy backups missing from the [Windows backup folder](#backup-and-restore).
11. Show `Lyrveil è pronto: http://localhost:4200` with any warnings.

Delivered in P01.6b, in `scripts/lib/Lifecycle.psm1` ([evidence](verification/2026-11-06-lifecycle.md)). It takes no
option.

- Steps 1 and 2 run the rows of [Preflight checks](#preflight-checks) that the scripts judge, except Git: Docker daemon,
  Compose, Engine API, memory seen by Docker and free disk. A failure exits 2.
- Step 5 runs before step 4 so that a clean installation passes: when `.env`, a volume, an image or a runtime container
  is missing, start lists what is missing and runs setup, and returns setup's status if setup fails. Step 4 then
  prints the data and models volume rows. A volume without the label exits 2 without running setup.
- Step 3 runs the orchestrator's preflight in a one-off container with `docker compose run --rm --no-deps -T`. A failed
  row, or Docker failing to run the container, exits 2. This step has not run against a real GPU yet: the tests skip it.
- Step 7 starts only `lyrveil-backend` and `lyrveil-frontend`, with `docker compose up --detach --no-build --wait
  --wait-timeout 120`. The orchestrator is not started until P02, because `lyrveil_orchestrator.main` does not exist
  and its container exits at once; start prints a warning for it.
- Step 8: a service that is unhealthy, or not healthy within 120 s, exits 3 with the last 50 log lines of both
  services. After the health checks, start sends a GET to the published port of the frontend on `127.0.0.1` and exits 3
  if it does not answer.
- Steps 6, 9 and 10 are not delivered: the model catalog and the runtimes come with P02, and the backup copy at start
  with P03.7b; until then `scripts\lyrveil.cmd backup` copies the backups. Each prints a `WARNING` line and never fails
  the start.
- Step 11 prints a summary of the warnings, then the ready line as the last line. The URL uses the port that the
  Compose file publishes, 4200 by default.

### `stop.cmd`

1. `python -m lyrveil.cli drain --timeout 60` inside the backend: pauses the queue and lets a running reply finish.
2. `POST /v1/drain` to stop the runtime and verify release.
3. Copy backups missing from the Windows backup folder.
4. `docker compose --profile ai stop --timeout 40`.

It succeeds when services are already stopped. It never runs `down -v`, and the volumes are external, so even that
command would keep them.

Delivered in P01.6b: step 4, which keeps the containers. Steps 1 to 3 print a `WARNING` line, because the queue and
the lifecycle manager do not exist yet, and the backup copy at stop comes with P03.7b. Stop never runs `docker compose down` in any form. It exits
0 when the services are stopped, also when they were stopped already or Docker is not running, and 1 when
`docker compose stop` fails or an argument is given.

### `restart.cmd`

`stop.cmd` followed by `start.cmd`.

Delivered in P01.6b. It returns the status of stop when stop fails, and the status of start otherwise.

### `status.cmd`

Prints services and health, GPU state, VRAM, queue sizes, active model, launch level, model verification, disk use and the URL. Option `-Json` gives machine-readable output.

Delivered in P01.6b: every service of the Compose file except the one-shot tools, with its state (`missing` when it has
no container) and health, the URL and whether it answers, whether Lyrveil is ready (backend and frontend running and
healthy, and the URL answering), and the free space on the drive of `%LOCALAPPDATA%`. GPU state, VRAM, queue sizes,
active model, launch level and model verification are not available yet: the text output prints a `WARNING` line for
each, and the JSON has them as `null` with the reasons in `warnings`.

```json
{
  "project": "lyrveil",
  "url": "http://localhost:4200",
  "app_answers": true,
  "ready": true,
  "services": [{"name": "lyrveil-backend", "state": "running", "health": "healthy", "started_by_start": true}],
  "gpu": null,
  "queues": null,
  "active_model": null,
  "model_verification": null,
  "disk": {"drive": "C:", "free_bytes": 123456789},
  "warnings": ["Queue sizes: not available yet, because the job queue comes with P02"]
}
```

It exits 0 when it printed the report, whatever the state of the services, and 2 when Docker is not running; with
`-Json` it then prints `{"docker_reachable": false}`.

### `logs.cmd`

```powershell
.\logs.cmd
.\logs.cmd lyrveil-backend -Follow
.\logs.cmd lyrveil-orchestrator -Since 10m
```

A thin wrapper around `docker compose logs`. Logs hold no private text.

Delivered in P01.6b with `-Follow`, `-Since <time>` and `-Tail <lines>`, and any number of service names. It exits 0
when Compose succeeds, 1 on an unknown argument, an option without its value or a Compose failure such as an unknown
service, and 2 when Docker is not running.

## Preflight checks

The same checks run in `start.cmd` and in `GET /system/preflight`.

| Check | Pass | Warning | Failure |
| --- | --- | --- | --- |
| Docker daemon | Reachable | | Unreachable |
| Compose | At least v2.30 | | Older |
| Engine API | At least 1.45, for volume subpaths | | Older |
| GPU in container | NVML works | | NVML fails |
| CUDA driver version | At least 13.0 | | Older |
| Total VRAM | At least 11000 MiB | 10000 to 10999 MiB | Under 10000 MiB |
| Free VRAM | At least 9000 MiB | Under 9000 MiB. Below 6000 MiB the model waits until memory is free, so startup continues | Never a failure, because other programs can free memory later |
| Memory seen by Docker | At least 20 GiB | 11 to 19.9 GiB | Under 11 GiB |
| Free disk | At least 20 GiB | 5 to 19.9 GiB | Under 5 GiB |
| Data volume | `lyrveil-data` exists with the label | | Missing |
| Models volume | `lyrveil-models` exists with the label | | Missing |
| Model files | Exact size | | Missing or wrong size |

The scripts read free disk on the Windows drive. The app reads free space inside Docker's disk image.

The orchestrator judges four rows: GPU in container, CUDA driver version, total VRAM and free VRAM, in
`orchestrator/src/lyrveil_orchestrator/preflight.py` (P01.4c). `python -m lyrveil_orchestrator.preflight` prints one
line per row, such as `WARNING  Free VRAM: 7000 MiB, under 9000 MiB: ...`, or the report of
[`GET /v1/preflight`](api.md#control-api) with `--json`. It exits 0 when no row fails, warnings included, and 1 when a
row fails. When NVML fails, the three other rows fail too, as not checked, because a value that was never read cannot
pass. These rules are unit-tested with a fake probe; the command has not yet run against the real card.

`setup.cmd` judges six rows in `scripts/lib/Setup.psm1` (P01.6a): Git, Docker daemon, Compose, Engine API, memory seen
by Docker and free disk. It prints them in the same format, with the advice for each failure on the next line, such as
the `.wslconfig` values for the machine's RAM when memory is too low. A row that needs the daemon fails as not checked
when the daemon is unreachable. `start.cmd` judges the same rows except Git, then the data and models volume rows in
`scripts/lib/Lifecycle.psm1` (P01.6b), and runs the orchestrator's four rows in a container. The model file row is
planned for the backend, with the catalog (P02).

The memory failure can be overridden with `LYRVEIL_ALLOW_LOW_MEMORY=true`. That setup is unsupported, and image generation may fail.

## Reference Compose file

`compose.yaml` holds the three core services, the four networks of [ADR 0003](decisions/0003-service-topology-and-networks.md)
that they use, and the two external volumes (P01.2), and, since P01.6c, the `lyrveil-models` service of the `tools`
profile with the network `lyrveil-download`, as below, and, since P02.1, the text runtime `lyrveil-llm` of the `ai`
profile, exactly as below. `lyrveil-image` is still the specification for P05 and is not in `compose.yaml` yet. The
`lyrveil-llm` image builds and its launcher runs without a GPU ([Runtime images](#runtime-images)); no container of
the service has started with a GPU and a model yet. Images are built locally, and
`pull_policy: never` keeps runtime fully offline. `lyrveil-models` has run only in the T-DCK-11 test project, against
a local HTTPS server.

`docker compose config --quiet` and `docker compose build` pass for the three core services. No container has been
started from them yet, and the health checks, the GPU reservation and the Docker socket mount have not run.

Differences between `compose.yaml` and the file below, all in the core services:

- `env_file` is `{path: .env, required: false}`, so `config` and `build` work before `setup.cmd` has created `.env`.
  Without `.env` the backend stops with exit status 78 and names `LYRVEIL_CONTROL_TOKEN`.
- The backend and orchestrator builds name `target: runtime`, because the last stage of `orchestrator/Dockerfile` would
  otherwise be the test stage.
- `orchestrator/Dockerfile` has a `runtime` stage, but `lyrveil_orchestrator.main` does not exist yet (P02). The
  image builds, and a container started from it exits at once until then.

```yaml
name: lyrveil

x-common: &common
  init: true
  security_opt: ["no-new-privileges:true"]
  cap_drop: ["ALL"]
  pull_policy: never
  logging:
    driver: json-file
    options: {max-size: "10m", max-file: "5"}

services:
  lyrveil-frontend:
    <<: *common
    container_name: lyrveil-frontend
    image: lyrveil/frontend:local
    build: {context: ., dockerfile: frontend/Dockerfile}
    restart: unless-stopped
    user: "10001:10001"
    read_only: true
    tmpfs: [/tmp]
    ports: ["${LYRVEIL_BIND_ADDRESS:-127.0.0.1}:${LYRVEIL_HTTP_PORT:-4200}:8080"]
    networks: [lyrveil-edge, lyrveil-app]
    depends_on:
      lyrveil-backend: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 10s

  lyrveil-backend:
    <<: *common
    container_name: lyrveil-backend
    image: lyrveil/backend:local
    build: {context: ., dockerfile: backend/Dockerfile}
    restart: unless-stopped
    user: "10001:10001"
    read_only: true
    tmpfs: [/tmp]
    env_file: [.env]
    volumes:
      - lyrveil-data:/data
      - lyrveil-models:/models:ro     # Lists and hashes files for custom profiles
    networks: [lyrveil-app, lyrveil-control, lyrveil-runtime]
    stop_grace_period: 30s
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2)"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 30s
      start_interval: 2s

  lyrveil-orchestrator:
    <<: *common
    container_name: lyrveil-orchestrator
    image: lyrveil/orchestrator:local
    build: {context: ., dockerfile: orchestrator/Dockerfile}
    restart: unless-stopped
    user: "10001:10001"
    group_add: ["0"]           # The Docker socket is root:root, mode 660, inside containers
    read_only: true
    tmpfs: [/tmp]
    gpus: all                  # NVML only. No model runs here and no VRAM is allocated
    env_file: [.env]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    networks: [lyrveil-control]
    stop_grace_period: 60s
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8100/v1/health', timeout=2)"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 15s

  lyrveil-llm:
    <<: *common
    profiles: [ai]
    container_name: lyrveil-llm
    image: lyrveil/llm:local
    build: {context: ., dockerfile: docker/llm/Dockerfile}
    restart: "no"
    user: "10001:10001"
    gpus: all
    read_only: true
    tmpfs: [/tmp]
    volumes:
      - {type: volume, source: lyrveil-models, target: /models/llm, read_only: true, volume: {subpath: llm}}
      - {type: volume, source: lyrveil-data, target: /run/lyrveil, read_only: true, volume: {subpath: runtime}}
    networks: [lyrveil-runtime]
    stop_grace_period: 20s
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:5001/api/v1/model"]
      interval: 5s
      timeout: 3s
      retries: 3
      start_period: 240s
      start_interval: 2s

  lyrveil-image:
    <<: *common
    profiles: [ai]
    container_name: lyrveil-image
    image: lyrveil/image:local
    build: {context: ., dockerfile: docker/image/Dockerfile}
    restart: "no"
    user: "10001:10001"
    gpus: all
    read_only: true
    environment:
      HOME: /tmp/comfy/home
    volumes:
      - {type: volume, source: lyrveil-models, target: /opt/ComfyUI/models/checkpoints, read_only: true, volume: {subpath: image/checkpoints}}
      - {type: volume, source: lyrveil-data, target: /run/lyrveil, read_only: true, volume: {subpath: runtime}}
      - type: tmpfs
        target: /tmp/comfy
        tmpfs: {size: 4294967296}
    networks: [lyrveil-runtime]
    stop_grace_period: 30s
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8188/system_stats', timeout=2)"]
      interval: 5s
      timeout: 3s
      retries: 3
      start_period: 180s
      start_interval: 2s

  lyrveil-models:
    <<: *common
    profiles: [tools]
    image: lyrveil/backend:local
    restart: "no"
    user: "10001:10001"
    read_only: true
    tmpfs: [/tmp]
    entrypoint: ["python", "-m", "lyrveil.tools.models"]
    volumes:
      - lyrveil-models:/models
    networks: [lyrveil-download]

networks:
  lyrveil-edge: {driver: bridge}
  lyrveil-app: {internal: true}
  lyrveil-control: {internal: true}
  lyrveil-runtime: {internal: true}
  lyrveil-download: {driver: bridge}   # Only the one-shot download tool joins it

volumes:
  lyrveil-data: {external: true}       # Created by setup.cmd, so down -v cannot remove it
  lyrveil-models: {external: true}
```

Notes:

- `depends_on` is not used for the runtimes, because the orchestrator starts them.
- When a runtime needs a writable path under read-only root, P02 and P05 add a tmpfs and record it here. The text
  runtime needs only `/tmp` without a GPU (P02.1); its first model load in P02 confirms that with a GPU.
- The volume subpaths `llm/` and `runtime/` must exist when a runtime container starts. Setup builds the runtime image
  and creates its container without starting it.
- `compose.test.yaml` (delivered in P01.8b) replaces the runtimes with the fakes of `tests/fakes/` and has no `gpus`
  and no Docker socket. It is a separate project, `lyrveil-test`, with its own port (`LYRVEIL_TEST_HTTP_PORT`, default
  14200), volumes, image tags and settings, so it never touches `lyrveil-data`, `lyrveil-models` or the backup folder.
  See [Fake runtimes](testing.md#fake-runtimes).
- No service bind-mounts a Windows folder. On the reference machine a folder on `C:` was 7 to 100 times slower than a
  volume ([ADR 0029](decisions/0029-windows-operation-with-docker-volumes.md)). Source code reaches containers only
  through image builds.
- The named container identity is verified with Compose labels. See [Security](security.md#docker-socket).

## Runtime images

### `docker/llm/Dockerfile`

Delivered in P02.1. Two stages, both on `debian:trixie-slim` pinned by digest:

1. `download`, with `curl` and `ca-certificates`: download `koboldcpp-linux-x64` from the pinned release, then stop the
   build unless its size is 628,981,944 bytes and its SHA-256 is the pin of [Technology](technology.md). The expected
   values are build arguments declared after the download, so a build with other values reuses the cached download.
2. Still in `download`: run `./koboldcpp-linux-x64 --unpack /opt/koboldcpp`, make the result readable for every user,
   and run `koboldcpp-launcher --help`, which stops the build when the unpacked launcher cannot start.
3. `runtime`, with `curl` for the health check: copy `/opt/koboldcpp` only, so the single-file binary never reaches
   the image, and `docker/llm/entrypoint.sh` as `/usr/local/bin/lyrveil-llm-entrypoint`. `HOME` is `/tmp` and the user
   is `10001:10001`.

`entrypoint.sh` reads `/run/lyrveil/llm-launch.env` as data, never with `source`. It skips blank lines and lines that
start with `#`, and requires every other line to be `KEY=VALUE`, each key of the
[launch file table](configuration.md#launch-files) exactly once with an allowed value, and `/models/llm/${MODEL_FILE}`
to be a readable file. Otherwise it prints the reason, naming the key and line but never the value, and exits 78.
Then it runs:

```bash
moe_args=()
if [ "${MOE_CPU_LAYERS}" -gt 0 ]; then
  moe_args=(--moecpu "${MOE_CPU_LAYERS}")
fi
if [ "${USE_MMAP}" = "1" ]; then
  moe_args+=(--usemmap)
fi
if [ "${SWA_PADDING}" -gt 0 ]; then
  moe_args+=(--swapadding "${SWA_PADDING}")
fi
if [ "${SMART_CACHE}" -gt 0 ]; then
  moe_args+=(--smartcache "${SMART_CACHE}")
fi
exec /opt/koboldcpp/koboldcpp-launcher \
  --model "/models/llm/${MODEL_FILE}" \
  --usecuda \
  --contextsize "${CONTEXT_SIZE}" \
  --gpulayers "${GPU_LAYERS}" \
  --batchsize "${BATCH_SIZE}" \
  --quantkv "${KV_QUANT}" \
  "${moe_args[@]}" \
  --host 0.0.0.0 \
  --port 5001 \
  --skiplauncher \
  --quiet
```

The image is the same for every text profile. Only the launch file changes.

`lyrveil-llm-entrypoint --print-command` runs the same checks, then prints that command, one argument per line, and
exits 0 without starting it. Any other argument exits 64.

`tests/docker/TextRuntime.Tests.ps1` confirms the unpacked launcher on a read-only root with only the tmpfs `/tmp`
(mounted `noexec` by default) as user 10001, with no GPU and no model: `--version` prints `1.124.2` and `--help` lists
every flag above. Whether a model load on a GPU needs any other writable path is checked by the first real load in
P02; a path it needs gets a tmpfs recorded in the Compose notes above. If the launcher needs the single-file binary
after all, the image keeps that binary, the root filesystem stays read-only and a tmpfs with exec permission holds the
extraction.

### `docker/image/Dockerfile`

1. Base `python:3.12.16-slim-trixie`.
2. Clone `Comfy-Org/ComfyUI` at commit `426f5eea52a7ece1dd599fff4cb6cd1f352ddd24` and remove `.git`.
3. Install `docker/image/requirements.lock`, which pins PyTorch for CUDA 13.0 and all of ComfyUI's dependencies with hashes.

The command reads `RESERVE_VRAM_GB` from the launch file and runs:

```bash
exec python main.py \
  --listen 0.0.0.0 --port 8188 \
  --disable-auto-launch --offline --disable-all-custom-nodes --disable-metadata \
  --preview-method none --log-stdout \
  --output-directory /tmp/comfy/output --input-directory /tmp/comfy/input \
  --temp-directory /tmp/comfy/temp --user-directory /tmp/comfy/user \
  --reserve-vram "${RESERVE_VRAM_GB}"
```

## Data layout

```text
Volume lyrveil-data, mounted at /data in the backend
  db/lyrveil.sqlite3         Database, with -wal and -shm files
  media/                     Images, thumbnails, assets. See Image generation
  runtime/                   Launch files
  backups/                   The newest 7 backups
  pre-restore-<time>/        db, media and runtime as they were before a restore, kept until you delete them
Volume lyrveil-models, mounted read-only at /models in the backend
  llm/                       Text model files, for example gemma-4-26B_q4_0-it.gguf
  image/checkpoints/         Image checkpoints, for example Illustrious-XL-v2.0.safetensors
  .verified.json             Size, modification time and hash of each verified file
Windows
  <repository>\              Code, compose.yaml and .env
  %USERPROFILE%\Documents\Lyrveil\backups\   Exported backups, set by LYRVEIL_BACKUP_DIR
```

## Backup and restore

| Action | Command | Content |
| --- | --- | --- |
| Backup | `.\scripts\lyrveil.cmd backup`, or **Impostazioni > Dati** | Consistent database copy through the SQLite backup API, media and launch files |
| List | `dir $env:USERPROFILE\Documents\Lyrveil\backups` | |
| Restore | `.\scripts\lyrveil.cmd restore <file>` | Replaces data after checking the manifest |
| Export character | Character profile | [JSON format](character-system.md#exchange-format) |
| Export conversation | Conversation menu | JSON or Markdown |

A backup is `lyrveil-<UTC time>.tar` with `manifest.json` (schema revision, app version, SHA-256 of each file). It
includes private content and is not encrypted, so store it accordingly.

The backend makes one backup a day at its first idle moment, and keeps the newest 7 in the volume. `start.cmd`,
`stop.cmd` and the backup command copy every backup that is missing from the Windows backup folder, which Lyrveil never
prunes. Resetting Docker Desktop's data or uninstalling it deletes the volumes, so the Windows folder is what limits the
loss to one day.

Restore steps:

1. Verify the manifest in a container with no network and the file's folder mounted read-only. Refuse a schema newer
   than the installed app.
2. Run `stop.cmd`.
3. Move `db` and `media` in the data volume to `pre-restore-<time>/`.
4. Extract and verify hashes.
5. Run `start.cmd`, which applies any pending migrations.

An older-schema backup migrates forward at start. The backend also creates a backup before migrations.

Delivered in P01.6d, in `scripts/lib/DataFiles.psm1` and `backend/src/lyrveil/cli.py`
([evidence](verification/2026-11-07-data-files.md)): the `backup` and `restore` commands and the `cli.py` commands
`backup`, `export-backups`, `restore-check` and `restore`. Every container runs the local backend image with
`--network none --read-only --cap-drop ALL` as `10001:10001`. Not delivered yet: the daily backup and the pruning to 7
in the volume, **Impostazioni > Dati**, the copy at start and stop, and the pre-migration backup (P03.7b); the T-DCK-12
round trip with real content is verified in P03.7b.

- The backup folder is `LYRVEIL_BACKUP_DIR` from `.env`, with `%VARIABLES%` expanded, or
  `%USERPROFILE%\Documents\Lyrveil\backups` when it is empty. A value that is not an absolute folder on a drive, or
  holds a comma or a double quote, exits 1. The folder is created when missing.
- `backup` takes no argument. Docker not running, a missing or unlabelled data volume and a missing backend image
  exit 2. `python -m lyrveil.cli backup`, with only the data volume mounted, copies `db/lyrveil.sqlite3` with the SQLite
  backup API, so the backend may keep running, and adds every file of `media/`, except `media/.staging`, and of
  `runtime/`. It writes `backups/lyrveil-<UTC time>.tar` under a temporary name, runs the same check as
  `restore-check` on it, and only then gives it its name. A database that does not exist yet, or has no Alembic
  revision, exits 1. `python -m lyrveil.cli export-backups` then copies, with the data volume read-only and the
  Windows folder as its only writable mount, every `lyrveil-*.tar` that the folder lacks by name, through a `.part`
  file. Nothing in the folder is overwritten or deleted.
- `manifest.json`, the first member, holds `format` (`lyrveil-backup`), `format_version` 1, `created_at`,
  `app_version`, `schema_revision` (the Alembic revision of the copied database) and, for each file, `path`, `bytes`
  and `sha256`.
- `restore <file>` takes exactly one file. Step 1 is `python -m lyrveil.cli restore-check` with only the file's folder
  mounted, read-only. It refuses, with exit status 1 and without stopping anything, a file that is not an uncompressed
  tar, a missing or invalid manifest, an entry the manifest does not list or one that is not a regular file, a path
  outside `db/lyrveil.sqlite3`, `media/` and `runtime/`, a size or SHA-256 that differs, and a schema revision that the
  installed migrations do not know, which means a newer app. Then the command asks; no answer exits 5. Step 2 runs
  `stop.cmd`. Steps 3 and 4 are one `python -m lyrveil.cli restore`: it checks again, extracts to `.restore-<time>/`
  in the data volume with every hash checked once more, then moves `db`, `media` and `runtime` to
  `pre-restore-<time>/` and the extracted ones into place with renames. A failed rename puts everything back, and any
  failure exits 1 with Lyrveil stopped. `backups/` and every other folder stay. Step 5 runs `start.cmd` and returns its
  status.

## Upgrade

```powershell
.\scripts\lyrveil.cmd backup
git pull
.\setup.cmd -Rebuild
.\restart.cmd
```

Data is preserved. Runtime upgrades change pinned versions in the repository and go through the GPU tests first.

## Uninstall

Stop services, remove containers and images. Data stays until you delete it yourself.

```powershell
.\stop.cmd
docker compose --profile ai --profile tools down --rmi local
```

The volumes `lyrveil-data` and `lyrveil-models` hold your conversations, images and weights, and they stay. Remove them
only on purpose with `docker volume rm lyrveil-data lyrveil-models`. Deleting them is permanent. Exported backups stay in
the Windows folder.

## Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| A container script fails with `bad interpreter: ^M` | Git converted line endings despite `.gitattributes` | Clone again with `git clone -c core.autocrlf=false` |
| "Running scripts is disabled on this system" | A `.ps1` file was started directly | Use the `.cmd` files, which bypass the policy for their own process only |
| `could not select device driver "nvidia"` | Docker Desktop has no GPU support | Update Windows driver and WSL with `wsl --update`, and use the WSL2 backend |
| `nvidia-container-cli : initialization error` | Docker daemon state | Restart Docker Desktop |
| Preflight: memory too low | `.wslconfig` absent or too small | Apply [WSL2 memory](#wsl2-memory) |
| Preflight: volume missing | Volumes removed, or Docker Desktop's data was reset | `.\setup.cmd`, then restore the newest backup from the Windows folder |
| Port 4200 in use | Another program | Set `LYRVEIL_HTTP_PORT` and restart |
| Model size or hash mismatch | Interrupted or changed file | `.\setup.cmd -VerifyModels`. Invalid files are downloaded again |
| "Altri programmi stanno usando la scheda video" | Games, browsers, video tools | Close them. Lyrveil retries by itself |
| Replies are slow | Partial GPU offload, low free VRAM | **Risorse AI** shows free VRAM and speed. Close GPU programs |
| Level B or C in use | Earlier out-of-memory | Free VRAM, then return to level A in **Impostazioni > Modelli** |
| `MODEL_FILE_MISSING` | The active profile's file was not downloaded or was moved | Run the command shown in **Impostazioni > Modelli**, or choose another profile |
| Replies are slow with a 26B profile | Too little free VRAM, or too many experts in RAM | **Risorse AI** shows the speed. Close GPU programs, or choose `gemma-4-12b` |
| State `ERROR` | Repeated failures | **Riprova** in **Risorse AI**, then `.\logs.cmd lyrveil-orchestrator` |
| `RUNTIME_CONTAINER_MISSING` | Runtime containers removed | `.\setup.cmd` |
| Black or broken image | VAE or model file problem | Regenerate. If it repeats, run `.\setup.cmd -VerifyModels` |
| Images look wrong for a character | Identity slots empty or too generic | Fill the slots in **Aspetto**, lock the right ones, then regenerate |
| Character answers in English | Language setting or weak examples | Check the conversation language and add Italian example exchanges |
| Character uses the wrong gender or address form | Field left on **Non indicato**, or a profile without **Qualità verificata** | Set the gender and *tu* or *Lei* on the card, and your own gender in **Impostazioni > Generali** |
| Time looks wrong in messages | Time zone | Set `LYRVEIL_TZ` and restart |
| Disk grows | Images, backups and Docker's disk image | Remove old exported backups. Review the gallery. Remove unused models with `.\scripts\lyrveil.cmd models -Remove <id>` |
