# Runtime contracts

Status: verified at the pinned upstream tags on 2026-11-01 by reading the official
source and API documents. KoboldCpp was verified again at `v1.124.2` on 2026-11-05, with
the facts for the model catalog. KoboldCpp's cache flags, ComfyUI's text encoding and its
perturbed-attention node were read on 2026-11-06. Nothing here has run against a loaded
model yet. Exit checks in P02, P05 and P06 confirm these contracts at runtime.

Lyrveil calls only what this page lists. To use anything else, verify it at the
pinned tag and add it here in the same commit.

## KoboldCpp v1.124.2

Sources: `koboldcpp.py` and `embd_res/kcpp_docs.embd` at tag `v1.124.1` on 2026-11-01.
`koboldcpp.py` and `kcpp_src/gpttype_adapter.cpp` at tag `v1.124.2` on 2026-11-05: every
flag and endpoint below is still present. The pin moved because the `v1.124.1` release was
withdrawn ([ADR 0028](decisions/0028-koboldcpp-pin-v1-124-2.md)).

### Flags

The image entrypoint launches `/opt/koboldcpp/koboldcpp-launcher`, extracted at build
time with `--unpack`, so each start skips self-extraction. On 2026-11-07 the launcher
unpacked from the pinned asset, run as user 10001 with no GPU, printed `1.124.2` for
`--version`, and its `--help` listed every flag of this table.

| Flag | Value | Purpose |
| --- | --- | --- |
| `--model` | `/models/llm/<MODEL_FILE>` | File of the active [profile](models.md#text-profiles). |
| `--usecuda` | No argument | CUDA acceleration. |
| `--contextsize` | From the profile, 16384 for Gemma 4 and 8192 for Mistral Nemo | Context window. Fallback values in [GPU orchestration](gpu-orchestration.md#load-failures-and-launch-levels). |
| `--gpulayers` | `-1` | Autofit, the upstream default. It adapts when other programs hold VRAM. Without `--moecpu`, the launcher also enables AutoFit with 1024 MB of padding, which places mixture-of-experts weights between GPU and RAM. |
| `--moecpu` | Only when `MOE_CPU_LAYERS` is above 0 | Keeps the expert weights of the first N layers in RAM. It disables AutoFit. Fallback for the mixture-of-experts profiles, see [Memory estimates](models.md#memory-estimates). |
| `--batchsize` | `BATCH_SIZE`, default `512` | Logical batch size, the upstream default. Accepts -1 and powers of two from 16 to 4096. P02 compares up to 2048. |
| `--quantkv` | `KV_QUANT`, default `f16` | KV cache type. Accepts `f16`, `bf16`, `q8_0`, `q5_1`, `q4_0`. Full effect needs Flash Attention, which is on by default at `v1.124.2`. |
| `--usemmap` | Only when `USE_MMAP` is 1 | Loads the model with mmap. Off by default upstream. |
| `--swapadding` | Only when `SWA_PADDING` is above 0 | Extra positions in the sliding-window cache. Upstream: it "affects the rewind limit before reprocessing is forced". Default 0. P04 compares 0, 1024 and 2048 for Gemma 4 ([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)). |
| `--smartcache` | `SMART_CACHE` slots, only when above 0 | When the cached prompt matches a new prompt by less than 70 %, saves the cached state to RAM and restores a slot that matches by more than 70 %. A value of 2 or more sets the slot count, and no value or 1 gives 5 slots, so Lyrveil never passes 1. Requires fast forwarding, which is on by default. P04 compares 0 and 2 on the target tier. |
| `--host` | `0.0.0.0` | Listens on the internal network only, because no port is published. |
| `--port` | `5001` | Upstream default port. |
| `--skiplauncher` | No argument | Never opens the GUI launcher. |
| `--quiet` | No argument | Hides generation inputs and outputs from logs. **Required for privacy.** |

Flash attention is on by default and needs no flag. These flags are never used:
`--remotetunnel`, `--websearch`, `--admin`, `--admindir`, `--sdmodel`, `--mcpfile`,
`--hordekey`, `--savedatafile`, `--preloadstory`, `--password`. Port isolation makes a
password unnecessary, and a shared secret would add a value to manage.

`--mmproj` is never passed, so the vision encoders of Gemma 4 are not loaded. `--moe-cache-mib` is not passed,
because the source marks it experimental. The batch, KV and mmap levers are measured in P02
([ADR 0030](decisions/0030-koboldcpp-efficiency-levers.md)), and SWA padding and SmartCache in P04
([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)).

`--noswa`, `--nofastforward`, `--noshift` and `--smartcontext` are never passed. `--noswa` keeps a full-size
sliding-window cache, about 2.8 GiB more VRAM for Gemma 4 26B-A4B at 16384 tokens. `--nofastforward` would
reprocess every prompt. `--smartcontext` is described upstream as outdated.

Source facts used by the design:

- Text model load happens before the HTTP API starts, so a reachable API implies a
  loaded model.
- A failed text model load exits with status 3 and the message `Could not load text model`.
- The prompt text is tokenized with `common_tokenize(ctx, text, add_bos, true)`. The last
  argument parses special tokens, so control tokens written in the prompt, such as
  `<|turn>` or `[INST]`, become the real tokens. BOS is added by the runtime, so prompts
  never contain it.
- Fast forwarding reuses the longest common prefix of the new prompt and the cached one.
- SWA turns on by itself for models that support it, such as Gemma 4, and context shifting
  is then off. The startup log says so.
- When the reuse point lies before what the sliding-window cache still holds, the runtime
  replays twice the window of cached tokens before that point (2,048 for Gemma 4) instead of
  the whole prompt. The source calls this recovery approximate, and the startup message warns
  that it can degrade recall with fast forwarding. If the cache cannot be rewound at all, the
  full prompt is processed.

### Endpoints

| Method and path | Use | Contract |
| --- | --- | --- |
| `GET /api/v1/model` | Readiness and identity | `{"result": "koboldcpp/<model name>"}` |
| `GET /api/extra/version` | Version check | Holds the string fields `result` and `version`. `version` must match the pinned release. |
| `GET /api/extra/true_max_context_length` | Real context size | Used to size budgets after a fallback. |
| `GET /api/extra/perf` | Monitoring | Includes `last_process`, `last_eval`, `last_token_count`, `stop_reason`, `queue`, `idle`, `uptime`. |
| `POST /api/extra/tokencount` | Tokenizer | Request `{"prompt": "...", "special": false}`. Response `{"value": N, "ids": [...], "prompt": "..."}`. `special` defaults to true and adds BOS. |
| `POST /api/extra/generate/stream` | Streaming reply | Takes `GenerationInput`. SSE with `event: message` and JSON `data` holding `token` and `finish_reason`. `finish_reason` is null until the last event, which has an empty `token` and `stop`, `length` or `error`. The server then closes the connection. There is no `[DONE]`. |
| `POST /api/v1/generate` | Structured tasks | Takes `GenerationInput`. Response `{"results": [{"text": "..."}]}`. |
| `POST /api/extra/abort` | Cancel, preempt | Request `{"genkey": "..."}`. Response `{"success": "true", "done": "true"}`. Upstream states that it does not work with several queued requests. The lease guarantees one request at a time. |

### GenerationInput fields used

| Field | Notes |
| --- | --- |
| `prompt` | Assembled by Lyrveil. |
| `max_context_length`, `max_length` | Set on every request. |
| `temperature`, `top_p`, `top_k`, `min_p` | Sampler values. |
| `rep_pen`, `rep_pen_range` | Repetition penalty. |
| `dry_multiplier`, `dry_base`, `dry_allowed_length`, `dry_penalty_last_n`, `dry_sequence_breakers` | DRY. Upstream defaults: 0.0, 1.75, 2, 320, empty. Multiplier 0 disables it. |
| `stop_sequence`, `trim_stop` | Text-level stops. `trim_stop` defaults to true. |
| `grammar` | GBNF string for structured output. `grammar_retain_state` stays false. |
| `genkey` | Unique per job. Needed for abort. |
| `sampler_seed` | Integer from 1 to 999999. Only in reproducible tests. |

`sampler_order` is left unset, so the upstream default applies. `json_schema` is not
part of `GenerationInput`. Lyrveil uses `grammar`.

## Text model facts

Read on 2026-11-05 unless stated. Memory and speed figures per profile are in
[Model catalog](models.md#memory-estimates).

### Gemma 4: `gemma-4-26b-a4b`, `gemma-4-12b`, `orion-26b-a4b`

| Fact | Value | Source |
| --- | --- | --- |
| Architecture | 26B-A4B: 30 layers, 128 experts, 8 active per token. 12B: 48 dense layers. Sliding window of 1024 tokens with periodic global layers | `config.json` of `google/gemma-4-26B-A4B-it` and `google/gemma-4-12B-it` |
| Chat template | `<\|turn>system\n…<turn\|>\n<\|turn>user\n…<turn\|>\n<\|turn>model\n`. With thinking off, the generation prompt ends with `<\|channel>thought\n<channel\|>` | Official `chat_template.jinja`, rendered in a sandbox. The GGUF templates of the 12B file and of Orion render the same text |
| Control tokens | `<\|turn>` 105, `<turn\|>` 106, `<\|channel>` 100, `<channel\|>` 101, `<\|think\|>` 98, all special | `tokenizer.json` |
| BOS, end tokens | `<bos>`. `generation_config.json` lists 1, 106 and 50 as end tokens | `tokenizer_config.json`, `generation_config.json` |
| Sampling advice | `temperature` 1.0, `top_p` 0.95, `top_k` 64 for all uses | Model card |
| Languages | More than 35 supported, more than 140 in pre-training. Italian is not named | Model card |
| Orion's template | "Gemma 4 31B template, thinking or non-thinking" | Orion card |

### Mistral v3 Tekken: `rocinante-x-12b`

| Fact | Value | Source |
| --- | --- | --- |
| Base | Mistral Nemo Instruct 2407, 40 layers, 8 key-value heads of size 128 | Model card, GGUF metadata |
| Template | `[INST]U1[/INST]A1</s>[INST]SYS\n\nU2[/INST]`. The system text joins the last user turn. No `[SYSTEM_PROMPT]` | Card, and the template in the GGUF rendered in a sandbox |
| Control tokens | `<s>` 1, `</s>` 2, `[INST]` 3, `[/INST]` 4, all special | `tokenizer_config.json` |

### ChatML: `rocinante-12b-v1.1`

Read on 2026-11-01.

| Fact | Value | Source |
| --- | --- | --- |
| Base | Mistral Nemo Base 2407: `MistralForCausalLM`, 40 layers, hidden size 5120, head dim 128, 8 key-value heads | `config.json` of the original model |
| Chat template in the GGUF | None | Hugging Face GGUF metadata |
| Formats listed by the author | ChatML for roleplay, Alpaca for story, Mistral | Original model card |
| `<\|im_start\|>`, `<\|im_end\|>` as special tokens | No. Absent from the 1000 entries of `added_tokens_decoder` | `tokenizer_config.json` |
| BOS, EOS | `<s>`, `</s>`. `add_bos_token` is true | `tokenizer_config.json` |
| Author's sampler advice | Use DRY. Temperature 0.7 is calmer, 1.2 is more intense | Original model card |
| Italian | Listed for the base model. Not stated for this finetune | Model cards |

ChatML markers are ordinary text in this model. Lyrveil writes them into the prompt and
stops on them with text-level stop sequences
([ADR 0025](decisions/0025-prompt-templates-per-model-family.md)).

## ComfyUI v0.43.0

Sources: `comfy/cli_args.py`, `nodes.py`, `README.md`,
`script_examples/websockets_api_example.py` at tag `v0.43.0`, and
`docs.comfy.org/development/comfyui-server`.

### Flags

| Flag | Value | Purpose |
| --- | --- | --- |
| `--listen` | `0.0.0.0` | Internal network only. |
| `--port` | `8188` | Upstream default. |
| `--disable-auto-launch` | No argument | No browser. |
| `--offline` | No argument | Disables partner nodes and blocks frontend Internet access through a restrictive CSP. |
| `--disable-all-custom-nodes` | No argument | Prevents loading any custom node. |
| `--disable-metadata` | No argument | Keeps prompts out of the PNG. Lyrveil stores metadata in the database. |
| `--preview-method` | `none` | No latent previews. |
| `--output-directory`, `--input-directory`, `--temp-directory`, `--user-directory` | `/tmp/comfy/...` | All in tmpfs, so ComfyUI keeps no state. |
| `--reserve-vram` | `1.5` | Leaves VRAM to the desktop. The idle reference machine uses 1489 MiB. |
| `--log-stdout` | No argument | Docker collects logs from stdout. |

Never used: `--enable-manager`, `--enable-assets`, `--multi-user`,
`--enable-cors-header`, `--tls-*`.

### Endpoints

| Method and path | Use | Contract |
| --- | --- | --- |
| `GET /system_stats` | Readiness, version, VRAM | System information. |
| `POST /prompt` | Submit | Body keys `prompt`, `client_id`, `prompt_id`. Success returns `prompt_id` and `number`. Validation failure returns `error` and `node_errors`. |
| `WS /ws?clientId=<id>` | Progress | See below. Binary frames are previews and are ignored. |
| `GET /history/{prompt_id}` | Outputs | Output nodes list `images` with `filename`, `subfolder`, `type`. |
| `GET /view` | Fetch file | Query `filename`, `subfolder`, `type`. |
| `POST /interrupt` | Cancel | Stops the current execution. |

WebSocket messages used:

| Type | Fields | Meaning for Lyrveil |
| --- | --- | --- |
| `execution_start` | `prompt_id` | Work began. |
| `executing` | `node`, `prompt_id` | Current node. `node` null means finished. Node `1` means the checkpoint loads. |
| `progress` | `node`, `prompt_id`, `value`, `max` | Sampler steps for the progress display. |
| `execution_success` | `prompt_id`, `timestamp` | Success. |
| `execution_error` | `prompt_id`, details | Failure. Text matching `OutOfMemoryError` maps to `IMAGE_OOM`. |
| `execution_interrupted` | `prompt_id`, `node_id`, `node_type`, `executed` | Cancelled. |

### Nodes used

| Class | Inputs |
| --- | --- |
| `CheckpointLoaderSimple` | `ckpt_name` |
| `CLIPSetLastLayer` | `clip`, `stop_at_clip_layer` |
| `CLIPTextEncode` | `clip`, `text` |
| `EmptyLatentImage` | `width`, `height`, `batch_size` |
| `KSampler` | `model`, `positive`, `negative`, `latent_image`, `seed`, `steps`, `cfg`, `sampler_name`, `scheduler`, `denoise` |
| `VAEDecode` | `samples`, `vae` |
| `VAEDecodeTiled` | `samples`, `vae`, `tile_size`, `overlap`, `temporal_size`, `temporal_overlap` |
| `ImageScaleBy` | `image`, `upscale_method` (`lanczos`), `scale_by` |
| `VAEEncode` | `pixels`, `vae` |
| `VAEEncodeTiled` | `pixels`, `vae`, `tile_size`, `overlap`, `temporal_size`, `temporal_overlap` |
| `SaveImage` | `images`, `filename_prefix` |

The three added nodes were checked in `nodes.py` at `v0.43.0` on 2026-11-05. They are core
nodes, so `--disable-all-custom-nodes` does not affect them.

Verified for measurement, not used unless P05 adopts it
([ADR 0036](decisions/0036-structured-image-scenes-and-measured-levers.md)):

| Class | Inputs | Fact |
| --- | --- | --- |
| `PerturbedAttentionGuidance` | `model`, `scale` (default 3.0, 0 to 100) | In `comfy_extras/nodes_pag.py`, loaded by `init_builtin_extra_nodes`, so `--disable-all-custom-nodes` does not affect it. Scale 0 returns the normal result. Otherwise it adds one model evaluation per step |

### Text encoding

Read in `comfy/sd1_clip.py` and `comfy/sdxl_clip.py` at `v0.43.0` on 2026-11-06. These facts shape the
[prompt builder](image-generation.md#caps-and-prompt-chunks) and its [sanitizer](image-generation.md#sanitizer).

| Fact | Detail |
| --- | --- |
| Weights | `(text)` multiplies the weight by 1.1, and `(text:1.5)` sets it. Parentheses nest |
| Escapes | `\(` and `\)` are literal parentheses |
| Embeddings | A word starting with `embedding:` loads a file from the embeddings folder. A missing file logs `warning, embedding:<name> does not exist, ignoring` |
| Chunks | Tokens are grouped in chunks of 77: start token, 75 tokens, end token. A group of 8 or more tokens can be split at the boundary. A shorter one moves whole to the next chunk |
| Concatenation | Each chunk is encoded, and the results are concatenated along the token axis |
| Pooled vector | Comes from the first chunk only. SDXL uses it as global conditioning |

## Image model facts

| Fact | Value | Source |
| --- | --- | --- |
| Illustrious XL v1.0 resolution | Native 1536x1536. Handles 512x512 to 1536x1536 and sizes like 1248x1824 | Model card |
| Illustrious prompt style | Natural language, Danbooru-style tags, or both | Model card |
| Illustrious XL v2.0 | Uploaded as "v2.0-STABLE", the more stable checkpoint of the last annealing phase. License field `creativeml-openrail-m`. No recommended settings | Model card, read 2026-11-05 |
| Illustrious sampler, steps, CFG | Not stated by any OnomaAI card or by the official user guide page | Model cards, `illustrious-xl.ai/updates/21` |
| Community guidance for Illustrious | Euler a, 20 to 28 steps, CFG 3 to 7, CLIP skip 2. Not official | Third-party guides, see [Image generation](image-generation.md#default-parameters) |
| NoobAI-XL 1.1 settings | CFG 5 to 6, 25 to 30 steps, Euler a. Total area about 1024x1024, best chosen from 768x1344, 832x1216, 896x1152, 1024x1024, 1152x896, 1216x832 and 1344x768. Positive starts with `masterpiece, best quality, newest, absurdres, highres, safe` | Model card, sizes read again 2026-11-06 |
| NoobAI-XL 1.1 caption order | Subject count (`1girl`, `1boy`, `1other`), character, series, artists, special tags, general tags, other tags. Quality tags by popularity percentile, date tags `old` to `newest` | Model card, read 2026-11-06 |
| LoRA and ControlNet | Modules trained on Illustrious v0.1 are stated to work with v1.0 | Model card |

## Docker Engine API

The orchestrator pins API version `v1.44`. The measured engine reports API 1.56 and
minimum 1.40. All calls use the Unix socket.

| Call | Use |
| --- | --- |
| `GET /v1.44/containers/{name}/json` | State, exit code, health, labels. |
| `POST /v1.44/containers/{name}/start` | Start a pre-created runtime. |
| `POST /v1.44/containers/{name}/stop?t=<seconds>` | SIGTERM, then SIGKILL after the grace period. |
| `GET /v1.44/containers/{name}/top` | Processes in a running container. |
| `GET /v1.44/containers/{name}/logs?stdout=1&stderr=1&tail=200` | Classify a load failure. Never stored verbatim. |

`{name}` is always `lyrveil-llm` or `lyrveil-image`. See
[Security](security.md#docker-socket).

`orchestrator/src/lyrveil_orchestrator/container_gate.py` implements these calls, delivered in P01.4b. Each method
inspects the container first and checks its identity, then sends its own call. It reads the answers as follows:

| Answer | Result |
| --- | --- |
| Inspect `200` | `State.Status`, `State.Running`, `State.Health.Status` and, for an exited or dead container, `State.ExitCode`. A missing or unknown field is refused |
| Start or stop `204`, `304` | Started or stopped, or already in that state |
| Top `409` | No process: Docker's answer for a container that is not running |
| Logs | Frames of `application/vnd.docker.multiplexed-stream` joined in order, or the body of `application/vnd.docker.raw-stream`, decoded as UTF-8 with invalid bytes replaced. Another media type, a truncated frame or a stream other than stdout and stderr is refused |
| `404` on any call | `ContainerMissingError`, which [recovery](gpu-orchestration.md#recovery) maps to `RUNTIME_CONTAINER_MISSING` |
| Socket unreachable, broken connection, timeout | `DockerUnavailableError`, which recovery maps to `DOCKER_UNAVAILABLE` |
| Any other status or body | `DockerResponseError` with the status code. Redirects are not followed |

The client waits 10 s for an answer, 60 s for a start, and the grace period plus 30 s for a stop, which answers only
once the container has exited. A stop accepts a grace period of 1 to 120 s. None of these readings has been measured
against the reference engine yet, because the runtime containers do not exist before P02. The `409` of top is the
least certain: the API reference does not list it.

## NVML and WSL2

Source: NVIDIA CUDA on WSL User Guide, confirmed by a container run on 2026-11-01.

- NVML has a limited feature set on WSL2. Upstream states that active compute process
  queries are not supported. `nvidia-smi --query-compute-apps` returned nothing in the
  container test.
- Device memory queries work: the container reported 12282 MiB total, 1489 MiB used
  and 10522 MiB free, matching the host.
- `libnvidia-ml.so.1` and `/dev/dxg` are present with `--gpus all`. `/dev/nvidia*` is
  absent. Never install a Linux NVIDIA driver in WSL2.

Calls used: `nvmlInit`, `nvmlDeviceGetHandleByIndex`, `nvmlDeviceGetName`,
`nvmlDeviceGetMemoryInfo`, `nvmlSystemGetDriverVersion`,
`nvmlSystemGetCudaDriverVersion`.

Consequence: NVML is a device-level memory gauge only. "Which process is active" comes
from Docker ([GPU orchestration](gpu-orchestration.md#verifying-gpu-release)).
