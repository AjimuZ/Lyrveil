# Operations

Status: accepted (2026-11-01), revised 2026-11-05 for model profiles, the 32 GB reference machine and operation from
Windows ([ADR 0029](decisions/0029-windows-operation-with-docker-volumes.md)). The scripts and files described here do not exist yet. They are delivered in phase P01 and completed through P06. This guide is their specification.

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
| KoboldCpp image | About 1.5 GB |
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

Delivered so far: `scripts\lyrveil.cmd` and `scripts\lyrveil.ps1` with the `test` command ([Testing](testing.md#commands)).
The other commands and the root entry points are planned. Today, an unknown or unbuilt command exits 1 with a message.

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

### `scripts\lyrveil.cmd import-model`

`scripts\lyrveil.cmd import-model <file>` copies a model file from Windows into the models volume, for a
[custom profile](models.md#custom-profiles). A `.gguf` goes to `llm/`, a `.safetensors` to `image/checkpoints/`. The
copy runs in a container with no network and the file's folder mounted read-only. Other extensions, names outside
`^[A-Za-z0-9._-]{1,128}\.(gguf|safetensors)$` and names already present are refused. The file is then registered in
**Impostazioni > Modelli**.

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

### `stop.cmd`

1. `python -m lyrveil.cli drain --timeout 60` inside the backend: pauses the queue and lets a running reply finish.
2. `POST /v1/drain` to stop the runtime and verify release.
3. Copy backups missing from the Windows backup folder.
4. `docker compose --profile ai stop --timeout 40`.

It succeeds when services are already stopped. It never runs `down -v`, and the volumes are external, so even that
command would keep them.

### `restart.cmd`

`stop.cmd` followed by `start.cmd`.

### `status.cmd`

Prints services and health, GPU state, VRAM, queue sizes, active model, launch level, model verification, disk use and the URL. Option `-Json` gives machine-readable output.

### `logs.cmd`

```powershell
.\logs.cmd
.\logs.cmd lyrveil-backend -Follow
.\logs.cmd lyrveil-orchestrator -Since 10m
```

A thin wrapper around `docker compose logs`. Logs hold no private text.

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
pass. These rules are unit-tested with a fake probe; the command has not yet run against the real card. The other rows
are planned for the scripts and the backend.

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
- When a runtime needs a writable path under read-only root, P02 and P05 add a tmpfs and record it here.
- `compose.test.yaml` replaces the runtimes with fakes and removes `gpus` for machines without a GPU.
- No service bind-mounts a Windows folder. On the reference machine a folder on `C:` was 7 to 100 times slower than a
  volume ([ADR 0029](decisions/0029-windows-operation-with-docker-volumes.md)). Source code reaches containers only
  through image builds.
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
Volume lyrveil-data, mounted at /data in the backend
  db/lyrveil.sqlite3         Database, with -wal and -shm files
  media/                     Images, thumbnails, assets. See Image generation
  runtime/                   Launch files
  backups/                   The newest 7 backups
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
