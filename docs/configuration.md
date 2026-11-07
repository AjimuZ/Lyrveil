# Configuration

Status: accepted (2026-11-01), revised 2026-11-05 for model profiles and operation from Windows. Implementation: the backend's reading of the deployment settings is delivered (P01.3a), and so is the creation of `.env` by `setup.cmd` (P01.6a); the rest is planned, phase P01 onward.

Configuration has three levels:

| Level | Where | Changed by | Applied |
| --- | --- | --- | --- |
| Deployment | `.env` | Editing the file, then `.\restart.cmd` | At container start |
| Runtime launch | `runtime/*.env` in the data volume | Model settings screen | At next runtime load |
| Application | `application_settings` table | Settings screens or API | At once, unless stated |

## Deployment settings

`setup.cmd` creates `.env` from `.env.example`, limits its access to the current Windows user, and fills the generated values. It never overwrites an existing `.env`. Today the only generated value is `LYRVEIL_CONTROL_TOKEN`. The file is UTF-8 without BOM, and its ACL has no inherited entry and grants only the current user, set with `icacls` before any content is written ([`setup.cmd`](operations.md#setupcmd)).

| Variable | Default | Description |
| --- | --- | --- |
| `LYRVEIL_BIND_ADDRESS` | `127.0.0.1` | Address of the published port. LAN exposure is unsupported, because the API has no authentication. |
| `LYRVEIL_HTTP_PORT` | `4200` | Published port. The backend accepts only `localhost` and `127.0.0.1` on this port as `Host` and `Origin` ([Security](security.md#local-only-model)). |
| `LYRVEIL_BACKUP_DIR` | Empty, meaning `%USERPROFILE%\Documents\Lyrveil\backups` | Windows folder for exported backups. Read by the scripts only. An absolute folder on a drive, without a comma or a double quote; `%VARIABLES%` are expanded. |
| `LYRVEIL_CONTROL_TOKEN` | Random 32 bytes, hex, set by `setup.cmd` | Bearer token of the control API. |
| `LYRVEIL_TZ` | `Europe/Rome` | Time zone of time context and scheduling. |
| `LYRVEIL_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LYRVEIL_ALLOW_LOW_MEMORY` | `false` | Turns the memory failure in preflight into a warning. Unsupported. |
| `LYRVEIL_MODEL_LICENSES_ACCEPTED` | `false` | Set by `setup.cmd` after the user accepts the model licenses. |

The backend reads these variables once at startup, in `backend/src/lyrveil/settings.py`, and ignores `LYRVEIL_BACKUP_DIR`. `LYRVEIL_CONTROL_TOKEN` is required and must be 64 hexadecimal characters, so the placeholder of `.env.example` is refused. A missing or invalid value stops the backend with exit status 78 and a message that names the variable without its value.

Changing `LYRVEIL_BACKUP_DIR` does not move existing backups. Live data and models are in the Docker volumes
`lyrveil-data` and `lyrveil-models`, which have no setting ([ADR 0029](decisions/0029-windows-operation-with-docker-volumes.md)).

Model files are not deployment settings. The active profiles are application settings, and the text model's file reaches
its container through the launch file ([Model catalog](models.md#selecting-a-profile)).

## Launch files

The backend writes these two files from the model settings. They are mounted read-only at `/run/lyrveil/` and read by the entrypoints when a container starts. Containers therefore never need recreating when a setting changes.

Only known keys with validated values are read. The entrypoint does not `source` the file, because that would execute content. It reads `KEY=VALUE` lines, skips blank lines and lines starting with `#`, and checks each value against a pattern. A missing file, an unknown, missing or repeated key, an invalid value or, for the text runtime, a missing model file makes the container exit with status 78, which the orchestrator reports as a configuration error. The text runtime's entrypoint (P02.1) requires every key of its table; the message names the key, never the value.

`runtime/llm-launch.env` in the data volume:

| Key | Default | Allowed |
| --- | --- | --- |
| `MODEL_PROFILE` | Default text profile of the tier | A catalog identifier or `custom-<12 hex>` |
| `MODEL_FILE` | File of that profile | `^[A-Za-z0-9._-]{1,128}\.gguf$`. The file must exist under `/models/llm/` |
| `CONTEXT_SIZE` | From the profile: `16384` for Gemma 4, `8192` for Mistral Nemo | `4096`, `6144`, `8192`, `12288`, `16384`, `24576` |
| `GPU_LAYERS` | `-1` | `-1` to `99` |
| `BATCH_SIZE` | `512` | `128`, `256`, `512`, `1024`, `2048` |
| `MOE_CPU_LAYERS` | `0`, meaning AutoFit decides | `0` to `99`. Above 0 adds `--moecpu` |
| `USE_MMAP` | `0` | `0`, `1`. `1` adds `--usemmap`. P02 decides per profile ([ADR 0030](decisions/0030-koboldcpp-efficiency-levers.md)) |
| `KV_QUANT` | `f16` | `f16`, `q8_0`. Passed as `--quantkv` |
| `SWA_PADDING` | `0` | `0`, `1024`, `2048`. Above 0 adds `--swapadding`. Gemma 4 profiles only. P04 decides per profile ([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)) |
| `SMART_CACHE` | `0` | `0`, `2`. Above 0 adds `--smartcache`. Target tier only. P04 decides per profile |
| `LEVEL` | `A` | `A`, `B`, `C`, `CUSTOM`. See [launch levels](gpu-orchestration.md#load-failures-and-launch-levels) |

`runtime/image-launch.env` in the data volume:

| Key | Default | Allowed |
| --- | --- | --- |
| `RESERVE_VRAM_GB` | `1.5` | `0` to `4`, step `0.5` |

## Application settings

Values are validated against a Pydantic model. `PATCH /settings` rejects unknown keys.

### General

| Key | Default | Range |
| --- | --- | --- |
| `user.display_name` | `Utente` | 1 to 40 chars |
| `user.grammatical_gender` | `non_indicato` | `femminile`, `maschile`, `non_indicato`. How characters address the user in Italian ([ADR 0033](decisions/0033-card-fields-for-a-model-independent-voice.md)) |
| `ui.language` | `it` | `it` |
| `characters.default_language` | `it` | `it`, `en`, `fr`, `de`, `es` |
| `app.first_start_notice_seen` | `false` | Boolean |
| `app.diagnostic_mode` | `false` | Boolean |

### Models

| Key | Default | Range |
| --- | --- | --- |
| `llm.profile_id` | `gemma-4-26b-a4b` on the target tier, `gemma-4-12b` on the reduced tier. Set by `setup.cmd` | A text profile allowed on the machine's tier |
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
| `llm.max_length` | 400 | 60 to 400. Ceiling of every [length band](prompting.md#length-bands). The reply reserve follows the character's band |

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
LYRVEIL_BACKUP_DIR=
LYRVEIL_CONTROL_TOKEN=generated-by-setup
LYRVEIL_TZ=Europe/Rome
LYRVEIL_LOG_LEVEL=INFO
LYRVEIL_ALLOW_LOW_MEMORY=false
LYRVEIL_MODEL_LICENSES_ACCEPTED=false
```

The repository holds only `.env.example`. A real `.env` is ignored by Git and Docker.
