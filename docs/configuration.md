# Configuration

Status: accepted (2026-11-01), revised 2026-11-05 for model profiles. Implementation: planned, phase P01 onward.

Configuration has three levels:

| Level | Where | Changed by | Applied |
| --- | --- | --- | --- |
| Deployment | `.env` | Editing the file, then `./restart.sh` | At container start |
| Runtime launch | `data/runtime/*.env` | Model settings screen | At next runtime load |
| Application | `application_settings` table | Settings screens or API | At once, unless stated |

## Deployment settings

`setup.sh` creates `.env` from `.env.example` with mode 600 and fills the generated values. It never overwrites an existing `.env`.

| Variable | Default | Description |
| --- | --- | --- |
| `LYRVEIL_BIND_ADDRESS` | `127.0.0.1` | Address of the published port. LAN exposure is unsupported, because the API has no authentication. |
| `LYRVEIL_HTTP_PORT` | `4200` | Published port. |
| `LYRVEIL_DATA_DIR` | `./data` | Persistent data. Must be on a Linux filesystem. `start.sh` refuses `/mnt/*`. |
| `LYRVEIL_MODELS_DIR` | `./models` | Weights. A path under `/mnt/*` only causes a warning. |
| `LYRVEIL_UID`, `LYRVEIL_GID` | Set by `setup.sh` from `id -u`, `id -g` | Containers run as this user, so data files stay accessible on the host. |
| `LYRVEIL_CONTROL_TOKEN` | Random 32 bytes, hex, set by `setup.sh` | Bearer token of the control API. |
| `LYRVEIL_TZ` | `Europe/Rome` | Time zone of time context and scheduling. |
| `LYRVEIL_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LYRVEIL_ALLOW_LOW_MEMORY` | `false` | Turns the memory failure in preflight into a warning. Unsupported. |
| `LYRVEIL_MODEL_LICENSES_ACCEPTED` | `false` | Set by `setup.sh` after the user accepts the model licenses. |

Changing `LYRVEIL_UID` or the data directory does not move or change existing files.

Model files are not deployment settings. The active profiles are application settings, and the text model's file reaches
its container through the launch file ([Model catalog](models.md#selecting-a-profile)).

## Launch files

The backend writes these two files from the model settings. They are mounted read-only at `/run/lyrveil/` and read by the entrypoints when a container starts. Containers therefore never need recreating when a setting changes.

Only known keys with validated values are read. The entrypoint does not `source` the file, because that would execute content. It reads `KEY=VALUE` lines and checks each value against a pattern. Unknown keys or invalid values make the container exit with status 78, which the orchestrator reports as a configuration error.

`data/runtime/llm-launch.env`:

| Key | Default | Allowed |
| --- | --- | --- |
| `MODEL_PROFILE` | Default text profile of the tier | A catalog identifier or `custom-<12 hex>` |
| `MODEL_FILE` | File of that profile | `^[A-Za-z0-9._-]{1,128}\.gguf$`. The file must exist under `/models/llm/` |
| `CONTEXT_SIZE` | From the profile: `16384` for Gemma 4, `8192` for Mistral Nemo | `4096`, `6144`, `8192`, `12288`, `16384`, `24576` |
| `GPU_LAYERS` | `-1` | `-1` to `99` |
| `BATCH_SIZE` | `512` | `128`, `256`, `512`, `1024` |
| `MOE_CPU_LAYERS` | `0`, meaning AutoFit decides | `0` to `99`. Above 0 adds `--moecpu` |
| `LEVEL` | `A` | `A`, `B`, `C`, `CUSTOM`. See [launch levels](gpu-orchestration.md#load-failures-and-launch-levels) |

`data/runtime/image-launch.env`:

| Key | Default | Allowed |
| --- | --- | --- |
| `RESERVE_VRAM_GB` | `1.5` | `0` to `4`, step `0.5` |

## Application settings

Values are validated against a Pydantic model. `PATCH /settings` rejects unknown keys.

### General

| Key | Default | Range |
| --- | --- | --- |
| `user.display_name` | `Utente` | 1 to 40 chars |
| `ui.language` | `it` | `it` |
| `characters.default_language` | `it` | `it`, `en`, `fr`, `de`, `es` |
| `app.first_start_notice_seen` | `false` | Boolean |
| `app.diagnostic_mode` | `false` | Boolean |

### Models

| Key | Default | Range |
| --- | --- | --- |
| `llm.profile_id` | `gemma-4-26b-a4b` on the target tier, `gemma-4-12b` on the reduced tier. Set by `setup.sh` | A text profile allowed on the machine's tier |
| `images.profile_id` | `illustrious-xl-v2.0` | An image profile |
| `images.quality` | `alta` | `alta`, `rapida` |
| `models.custom` | Empty list | Registered [custom profiles](models.md#custom-profiles): identifier, kind, file, size, SHA-256, template family |

### Text generation

Defaults come from the active profile's [sampler preset](prompting.md#sampler-presets). Values below are for the
`gemma4` preset, with the `nemo` preset in brackets. A changed value is stored for the active profile only.

| Key | Default | Range |
| --- | --- | --- |
| `llm.temperature` | 1.0 (0.8) | 0 to 2 |
| `llm.top_p` | 0.95 (0.95) | 0 to 1 |
| `llm.top_k` | 64 (0) | 0 to 200 |
| `llm.min_p` | 0 (0.05) | 0 to 1 |
| `llm.rep_pen` | 1.0 (1.05) | 1 to 1.5 |
| `llm.rep_pen_range` | Unset (512) | 0 to 4096 |
| `llm.dry_multiplier` | 0.8 | 0 to 4 |
| `llm.dry_base` | 1.75 | 1 to 4 |
| `llm.dry_allowed_length` | 2 | 1 to 20 |
| `llm.dry_penalty_last_n` | 320 | 0 to 4096 |
| `llm.max_length` | 400 | 50 to 1024. It also sets `context.reply_reserve_tokens` |

### Memory and context

Listed in [Memory engine](memory-engine.md#settings).

### Images

First-pass defaults come from the image profile's preset. A changed value is stored for the active profile only.

| Key | Default | Range |
| --- | --- | --- |
| `images.steps` | 28 | 10 to 50 |
| `images.cfg` | 5.5 | 1 to 12 |
| `images.sampler` | `euler_ancestral` | Allowlist |
| `images.scheduler` | `normal` | `normal`, `karras` |
| `images.clip_skip` | 2 | 1, 2 |
| `images.hires_scale` | 1.5 | 1.25 to 1.5 |
| `images.hires_denoise` | 0.45 | 0.3 to 0.6 |
| `images.hires_steps` | 14 | 8 to 30 |
| `images.auto_detect_requests` | `true` | Boolean |
| `images.contextual_enabled` | `false` | Boolean |
| `images.proposal_min_messages` | 20 | 5 to 200 |
| `images.max_wait_seconds` | 60 | 0 to 600 |
| `images.max_in_a_row` | 4 | 1 to 20 |

### GPU and jobs

Listed in [GPU orchestration](gpu-orchestration.md#timeouts), plus the following. The text model's VRAM thresholds
belong to its profile in the [catalog](models.md#catalog-file).

| Key | Default | Range |
| --- | --- | --- |
| `vram.required_image_mib` | 6500 | 1000 to 24000 |
| `vram.wait_seconds` | 120 | 0 to 3600 |
| `jobs.background_idle_seconds` | 20 | 0 to 600 |
| `jobs.priority.<kind>` | See [priorities](gpu-orchestration.md#job-kinds-and-priorities) | 0 to 100 |

The backend sends the settings the orchestrator needs when it connects.

### Proactive

Listed in [Scheduler](scheduler.md#per-conversation-settings).

## Example `.env`

```dotenv
LYRVEIL_BIND_ADDRESS=127.0.0.1
LYRVEIL_HTTP_PORT=4200
LYRVEIL_DATA_DIR=./data
LYRVEIL_MODELS_DIR=./models
LYRVEIL_UID=1000
LYRVEIL_GID=1000
LYRVEIL_CONTROL_TOKEN=generated-by-setup
LYRVEIL_TZ=Europe/Rome
LYRVEIL_LOG_LEVEL=INFO
LYRVEIL_ALLOW_LOW_MEMORY=false
LYRVEIL_MODEL_LICENSES_ACCEPTED=false
```

The repository holds only `.env.example`. A real `.env` is ignored by Git and Docker.
