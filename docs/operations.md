# Operations

Status: accepted (2026-11-01), revised 2026-11-05 for model profiles and the 32 GB reference machine. The scripts and files described here do not exist yet. They are delivered in phase P01 and completed through P06. This guide is their specification.

## Requirements

| Item | Target | Reduced tier | Not supported |
| --- | --- | --- | --- |
| Windows | Windows 11 | Windows 11 | Other systems are untested |
| GPU | RTX 4070, 12 GB | Same | Under 10000 MiB of VRAM |
| Windows RAM | 32 GB | 16 GB | Under 16 GB |
| WSL2 `memory` | `24GB` | `12GB` | Docker sees under 11 GiB |
| WSL2 `swap` | `8GB` | `16GB` | |
| Free disk | 50 GB for setup with the default profiles | 40 GB | |
| Software | Docker Desktop with WSL2 backend, an Ubuntu WSL distribution, Git | Same | |
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
| KoboldCpp image | About 1.5 GB |
| ComfyUI image with PyTorch and CUDA | About 8 to 12 GB |
| Frontend, backend and orchestrator images | Under 1 GB |
| Application data | Grows with use. About 2 to 3 MB per **Rapida** image and 4 to 6 MB per **Alta** image |

## First installation

### 1. Prepare WSL2 and Docker

From PowerShell:

```powershell
wsl --update
wsl --install -d Ubuntu-24.04
```

In Docker Desktop, open **Settings > Resources > WSL integration** and enable the Ubuntu distribution.

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

Then apply it from PowerShell. This stops all running WSL distributions and Docker Desktop containers:

```powershell
wsl --shutdown
```

Lyrveil's scripts never change this file. Preflight prints the right values when memory is too low.

### 2. Clone inside Ubuntu

Keep the repository on the Linux filesystem. SQLite in WAL mode is unreliable on `/mnt/c`, and `start.sh` refuses it.

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/AjimuZ/Lyrveil.git lyrveil
cd lyrveil
```

### 3. Run setup

```bash
./setup.sh
```

### 4. Start

```bash
./start.sh
```

Open `http://localhost:4200`.

## Scripts

The root scripts are thin entry points to `scripts/*.sh`. All use `set -euo pipefail`, are idempotent, and never delete persistent data.

| Exit status | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Unexpected failure |
| 2 | Preflight failed |
| 3 | Health check timed out |
| 4 | Model file missing or invalid |
| 5 | The user declined |

### `setup.sh`

Options: `--accept-model-licenses`, `--verify-models`, `--rebuild`, `--skip-models`.

1. Check Git, Docker, Compose and Docker's memory, and show the required space.
2. Refuse to continue from `/mnt/*`.
3. Create `.env` if missing, with a new token, UID and GID.
4. Create `data/` and `models/` subdirectories.
5. Pick the default text profile from the memory Docker sees ([ADR 0027](decisions/0027-hardware-tiers-after-ram-upgrade.md)).
   Show the licenses of the default profiles and ask for acceptance. See [Licensing](licensing.md#models).
6. Download and verify the default profiles with `scripts/download-models.sh`.
7. Write default launch files if missing, with the default text profile.
8. Build images: `docker compose --profile ai build`.
9. Create the runtime containers without starting them: `docker compose --profile ai create lyrveil-llm lyrveil-image`.

Running it again repeats only what is missing.

### `scripts/download-models.sh`

URLs, sizes and hashes come from `config/model-catalog.json` ([Model catalog](models.md#catalog-file)). Text files go
to `models/llm/`, image checkpoints to `models/image/checkpoints/`.

| Option | Downloads |
| --- | --- |
| None | The default text and image profiles of the machine's tier: 21.38 GB on the target tier, 13.91 GB on the reduced tier |
| `--model <id>` | One profile from the catalog. Repeatable. Asks for license acceptance unless `--accept-model-licenses` is given |
| `--all` | Every profile allowed on the machine's tier |
| `--list` | Prints the catalog with sizes, licenses and local state. Downloads nothing |

Behavior:

1. Show the required space for the chosen files and compare it with free space. Stop if insufficient.
2. Skip a file when `models/.verified.json` records the same size and modification time and a matching hash.
3. Download to `<file>.part` with `curl --fail --location --continue-at - --retry 5 --retry-delay 5`. Revisions are pinned, so a resume cannot mix two versions.
4. Check the size, then compute the SHA-256.
5. On a mismatch, keep nothing: delete the `.part` file and exit 4.
6. Move to the final name atomically and update `models/.verified.json`.

No account or token is needed, because every catalog repository is public and not gated. Downloads are independent of image builds, and weights never enter an image.

### `start.sh`

1. Verify Docker. Exit 2 with instructions if the daemon is off.
2. Verify Compose at least v2.30, and Engine API at least 1.44.
3. Verify GPU support with `docker compose run --rm --no-deps lyrveil-orchestrator python -m lyrveil_orchestrator.preflight`, which reads NVML and Docker facts. On a first run the image does not exist yet, so step 6 runs first and this check follows it.
4. Check persistent directories: exist, writable, not under `/mnt/*`.
5. Check the files of the active profiles: presence and exact size. A full hash only runs through `setup.sh --verify-models`.
6. Run setup if `.env`, images or runtime containers are missing.
7. Start core services: `docker compose up -d --no-build lyrveil-backend lyrveil-orchestrator lyrveil-frontend`.
8. Wait up to 120 s for health checks. Exit 3 with the last logs on failure.
9. The active text model starts automatically: the orchestrator's default mode is `llm`. Wait up to 300 s for `LLM_READY`. On timeout, print a warning and continue, because the app works for reading and editing.
10. Show `Lyrveil è pronto: http://localhost:4200` with any warnings.

### `stop.sh`

1. `python -m lyrveil.cli drain --timeout 60` inside the backend: pauses the queue and lets a running reply finish.
2. `POST /v1/drain` to stop the runtime and verify release.
3. `docker compose --profile ai stop --timeout 40`.

It succeeds when services are already stopped. It never runs `down -v`.

### `restart.sh`

`stop.sh` followed by `start.sh`.

### `status.sh`

Prints services and health, GPU state, VRAM, queue sizes, active model, profile in use, model verification, disk use and the URL. Option `--json` gives machine-readable output.

### `logs.sh`

```bash
./logs.sh
./logs.sh lyrveil-backend --follow
./logs.sh lyrveil-orchestrator --since 10m
```

A thin wrapper around `docker compose logs`. Logs hold no private text.

## Preflight checks

The same checks run in `start.sh` and in `GET /system/preflight`.

| Check | Pass | Warning | Failure |
| --- | --- | --- | --- |
| Docker daemon | Reachable | | Unreachable |
| Compose | At least v2.30 | | Older |
| Engine API | At least 1.44 | | Older |
| GPU in container | NVML works | | NVML fails |
| CUDA driver version | At least 13.0 | | Older |
| Total VRAM | At least 11000 MiB | 10000 to 10999 MiB | Under 10000 MiB |
| Free VRAM | At least 9000 MiB | Under 9000 MiB. Below 6000 MiB the model waits until memory is free, so startup continues | Never a failure, because other programs can free memory later |
| Memory seen by Docker | At least 20 GiB | 11 to 19.9 GiB | Under 11 GiB |
| Free disk | At least 20 GiB | 5 to 19.9 GiB | Under 5 GiB |
| Data directory | Linux filesystem | | `/mnt/*` |
| Models directory | Linux filesystem | `/mnt/*`, slower | Missing |
| Model files | Exact size | | Missing or wrong size |

The memory failure can be overridden with `LYRVEIL_ALLOW_LOW_MEMORY=true`. That setup is unsupported, and image generation may fail.

## Reference Compose file

`compose.yaml`, to be delivered in P01. Images are built locally, and `pull_policy: never` keeps runtime fully offline.

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
    user: "${LYRVEIL_UID}:${LYRVEIL_GID}"
    read_only: true
    tmpfs: [/tmp]
    env_file: [.env]
    volumes:
      - ${LYRVEIL_DATA_DIR:-./data}:/data
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
    user: "${LYRVEIL_UID}:${LYRVEIL_GID}"
    gpus: all
    read_only: true
    tmpfs: [/tmp]
    volumes:
      - ${LYRVEIL_MODELS_DIR:-./models}/llm:/models/llm:ro
      - ${LYRVEIL_DATA_DIR:-./data}/runtime:/run/lyrveil:ro
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
    user: "${LYRVEIL_UID}:${LYRVEIL_GID}"
    gpus: all
    read_only: true
    environment:
      HOME: /tmp/comfy/home
    volumes:
      - ${LYRVEIL_MODELS_DIR:-./models}/image/checkpoints:/opt/ComfyUI/models/checkpoints:ro
      - ${LYRVEIL_DATA_DIR:-./data}/runtime:/run/lyrveil:ro
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

networks:
  lyrveil-edge: {driver: bridge}
  lyrveil-app: {internal: true}
  lyrveil-control: {internal: true}
  lyrveil-runtime: {internal: true}
```

Notes:

- `depends_on` is not used for the runtimes, because the orchestrator starts them.
- When a runtime needs a writable path under read-only root, P02 and P05 add a tmpfs and record it here.
- `compose.test.yaml` replaces the runtimes with fakes and removes `gpus` for machines without a GPU.
- The named container identity is verified with Compose labels. See [Security](security.md#docker-socket).

## Runtime images

### `docker/llm/Dockerfile`

1. Base `debian:trixie-slim` with `curl` and `ca-certificates`.
2. Download `koboldcpp-linux-x64` from the pinned release and verify its SHA-256 during the build.
3. Run `./koboldcpp-linux-x64 --unpack /opt/koboldcpp`, then remove the single-file binary.
4. Copy `docker/llm/entrypoint.sh`.

`entrypoint.sh` reads `/run/lyrveil/llm-launch.env` as data, never with `source`. It checks every key against the
[allowed values](configuration.md#launch-files) and that `/models/llm/${MODEL_FILE}` exists, then runs:

```bash
moe_args=()
if [ "${MOE_CPU_LAYERS}" -gt 0 ]; then
  moe_args=(--moecpu "${MOE_CPU_LAYERS}")
fi
exec /opt/koboldcpp/koboldcpp-launcher \
  --model "/models/llm/${MODEL_FILE}" \
  --usecuda \
  --contextsize "${CONTEXT_SIZE}" \
  --gpulayers "${GPU_LAYERS}" \
  --batchsize "${BATCH_SIZE}" \
  "${moe_args[@]}" \
  --host 0.0.0.0 \
  --port 5001 \
  --skiplauncher \
  --quiet
```

The image is the same for every text profile. Only the launch file changes.

The P02 exit check confirms this launcher at the pinned version. If it needs the single-file binary after all, the image keeps that binary, the root filesystem stays read-only and a tmpfs with exec permission holds the extraction.

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
data/
  db/lyrveil.sqlite3         Database, with -wal and -shm files
  media/                     Images, thumbnails, assets. See Image generation
  runtime/                   Launch files
  backups/                   Backups
models/
  llm/                       Text model files, for example gemma-4-26B_q4_0-it.gguf
  image/checkpoints/         Image checkpoints, for example Illustrious-XL-v2.0.safetensors
  .verified.json             Size, modification time and hash of each verified file
```

## Backup and restore

| Action | Command | Content |
| --- | --- | --- |
| Backup | `./scripts/backup.sh`, or **Impostazioni > Dati** | Consistent database copy through the SQLite backup API, media and launch files |
| List | `ls data/backups/` | |
| Restore | `./scripts/restore.sh data/backups/<file>` | Replaces data after checking the manifest |
| Export character | Character profile | [JSON format](character-system.md#exchange-format) |
| Export conversation | Conversation menu | JSON or Markdown |

A backup is `data/backups/lyrveil-<UTC time>.tar` with `manifest.json` (schema revision, app version, SHA-256 of each file). It includes private content and is not encrypted, so store it accordingly.

Restore steps:

1. Verify the manifest and refuse a schema newer than the installed app.
2. Run `stop.sh`.
3. Move `data/db` and `data/media` to `data/pre-restore-<time>/`.
4. Extract and verify hashes.
5. Run `start.sh`, which applies any pending migrations.

An older-schema backup migrates forward at start. The backend also creates a backup before migrations, and keeps the newest 5.

## Upgrade

```bash
./scripts/backup.sh
git pull
./setup.sh --rebuild
./restart.sh
```

Data is preserved. Runtime upgrades change pinned versions in the repository and go through the GPU tests first.

## Uninstall

Stop services, remove containers and images. Data stays until you delete it yourself.

```bash
./stop.sh
docker compose --profile ai down --rmi local
```

`data/` and `models/` hold your conversations, images and weights. Deleting them is permanent.

## Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `bad interpreter: ^M` | The repository was cloned on Windows with converted line endings | Clone inside Ubuntu. `.gitattributes` forces LF |
| `could not select device driver "nvidia"` | Docker Desktop has no GPU support | Update Windows driver and WSL with `wsl --update`, and use the WSL2 backend |
| `nvidia-container-cli : initialization error` | Docker daemon state | Restart Docker Desktop |
| Preflight: memory too low | `.wslconfig` absent or too small | Apply [WSL2 memory](#wsl2-memory) |
| Preflight: `/mnt/c` refused | Repository on the Windows filesystem | Clone inside Ubuntu |
| Port 4200 in use | Another program | Set `LYRVEIL_HTTP_PORT` and restart |
| Model size or hash mismatch | Interrupted or changed file | `./setup.sh --verify-models`. Invalid files are downloaded again |
| "Altri programmi stanno usando la scheda video" | Games, browsers, video tools | Close them. Lyrveil retries by itself |
| Replies are slow | Partial GPU offload, low free VRAM | **Risorse AI** shows free VRAM and speed. Close GPU programs |
| Level B or C in use | Earlier out-of-memory | Free VRAM, then return to level A in **Impostazioni > Modelli** |
| `MODEL_FILE_MISSING` | The active profile's file was not downloaded or was moved | Run the command shown in **Impostazioni > Modelli**, or choose another profile |
| Replies are slow with a 26B profile | Too little free VRAM, or too many experts in RAM | **Risorse AI** shows the speed. Close GPU programs, or choose `gemma-4-12b` |
| State `ERROR` | Repeated failures | **Riprova** in **Risorse AI**, then `./logs.sh lyrveil-orchestrator` |
| `RUNTIME_CONTAINER_MISSING` | Runtime containers removed | `./setup.sh` |
| Black or broken image | VAE or model file problem | Regenerate. If it repeats, run `./setup.sh --verify-models` |
| Images look wrong for a character | Prompt too generic | Edit the persistent prompt, then regenerate |
| Character answers in English | Language setting or weak examples | Check the conversation language and add Italian examples |
| Time looks wrong in messages | Time zone | Set `LYRVEIL_TZ` and restart |
| Disk grows | Images and backups | Remove old backups. Review the gallery |
