# Model catalog

Status: accepted (2026-11-05), revised 2026-11-06 for verified-profile evidence and cache levers. Implementation: the catalog file and its validator are delivered (P02.1a), and so are the backend's text profile change and custom registration (P06.6a, [Delivered in P06.6a](#delivered-in-p066a)); the rest is planned, phases P02, P04, P05 and P06. Facts were read from the Hugging Face API, the model cards and the official chat templates on 2026-11-05. No model has been downloaded or loaded yet, so every memory and speed figure is an estimate.

The user chooses the active text and image models from verified profiles ([ADR 0024](decisions/0024-selectable-model-profiles.md)). One text model serves every text task, and it never shares the GPU with the image model ([ADR 0005](decisions/0005-exclusive-gpu-by-container-lifecycle.md)).

## Text profiles

| Profile | File | Source, pinned revision | Bytes | SHA-256 | License | Template | Tier |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gemma-4-26b-a4b` | `gemma-4-26B_q4_0-it.gguf` | `google/gemma-4-26B-A4B-it-qat-q4_0-gguf` at `d1c082be9cf3c8a514acf63b8761f4b41935842e` | 14,439,363,584 | `3eca3b8f6d7baf218a7dd6bba5fb59a56ee25fe2d567b6f5f589b4f697eca51d` | Apache 2.0 | `gemma4` | Target |
| `gemma-4-12b` | `gemma-4-12b-it-qat-q4_0.gguf` | `google/gemma-4-12B-it-qat-q4_0-gguf` at `29d097773436b69ff9feafd636ab4cf873786537` | 6,975,879,296 | `93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b` | Apache 2.0 | `gemma4` | Reduced |
| `orion-26b-a4b` | `TheDrummer_Orion-26B-A4B-v1.1-Q4_K_M.gguf` | `bartowski/TheDrummer_Orion-26B-A4B-v1.1-GGUF` at `ecc1d958b6b4a83445ab80262965e151e00d97ac` | 17,966,167,008 | `b0374debf3034de2c149ef4e3c3d3f30b67d27df0158ef620a445c2598754813` | Not stated | `gemma4` | Target |
| `rocinante-x-12b` | `TheDrummer_Rocinante-X-12B-v1-Q4_K_M.gguf` | `bartowski/TheDrummer_Rocinante-X-12B-v1-GGUF` at `4302599a9fecd0e250c3790d77a38f3bd791fdd4` | 7,477,207,712 | `1ca25853a19d6b15f9a94f7e5bc86f4cc88bd0c9dea12a00ea55452b4208ade6` | Not stated | `mistral_v3_tekken` | Reduced |
| `rocinante-12b-v1.1` | `Rocinante-12B-v1.1-Q4_K_M.gguf` | `bartowski/Rocinante-12B-v1.1-GGUF` at `9d15ca24c675a428fc5821cccc537f2a6f64987d` | 7,477,203,712 | `5133341741709aafcbc162c8fe638143e466288b7942ba7a0e9d9ef4e08d056f` | `other`, no terms | `chatml` | Reduced |

"Tier" is the smallest [hardware tier](decisions/0027-hardware-tiers-after-ram-upgrade.md) that runs the profile. A profile for the reduced tier also runs on the target tier. No repository is gated, and none needs an account.

| Profile | Base | What it is for |
| --- | --- | --- |
| `gemma-4-26b-a4b` | Gemma 4 26B-A4B instruct, mixture of experts: 25.2B parameters, 3.8B active, 30 layers | **Default on the target tier.** Best general quality that runs at chat speed on this card. Google's quantization-aware Q4_0 file |
| `gemma-4-12b` | Gemma 4 12B instruct, dense, 48 layers | **Default on the reduced tier.** Fits entirely in VRAM, so it is the fastest choice and the quickest to reload after an image |
| `orion-26b-a4b` | Gemma 4 26B-A4B, roleplay finetune by TheDrummer | Livelier character writing, same prompt format as the default |
| `rocinante-x-12b` | Mistral Nemo Instruct 2407, roleplay finetune by TheDrummer | Successor of the brief's model. Mistral Nemo lists Italian among its languages |
| `rocinante-12b-v1.1` | Mistral Nemo Base 2407, roleplay finetune | The model named in the brief, kept for continuity |

Neither the Gemma 4 cards nor the finetune cards list Italian by name. Gemma 4 states support for more than 35 languages and pre-training on more than 140. Italian quality is measured with the [protocol](prompting.md#italian-quality-protocol) for the default and for both roleplay profiles.

## Image profiles

All are SDXL checkpoints with epsilon prediction and an embedded VAE.

| Profile | File | Source, pinned revision | Bytes | SHA-256 | License |
| --- | --- | --- | --- | --- | --- |
| `illustrious-xl-v2.0` | `Illustrious-XL-v2.0.safetensors` | `OnomaAIResearch/Illustrious-XL-v2.0` at `69459c1fe6f46db41ab31e6114f05acc0e06bcaa` | 6,938,040,674 | `c2a1a3eaa13d4c107dc7e00c3fe830cab427aa026362740ea094745b3422a331` | `creativeml-openrail-m` per the card |
| `illustrious-xl-v1.0` | `Illustrious-XL-v1.0.safetensors` | `OnomaAIResearch/Illustrious-XL-v1.0` at `89d625482edf06d545b740265482f5c8fb2cadb0` | 6,938,040,736 | `735cf3fefcbdc4f7817f53247e38b836ffd27c7641af6d8daa21d245242cb4bd` | CreativeML Open RAIL++-M |
| `noobai-xl-1.1` | `NoobAI-XL-v1.1.safetensors` | `Laxhar/noobai-XL-1.1` at `814a274af2b8097c0828819d561ec74c7d0c6cea` | 7,105,349,958 | `6681e8e4b134c81f16533acedb0d406d7e5e366e1624b4105178c64d00b05d51` | Fair AI Public License 1.0-SD |

| Profile | What it is for |
| --- | --- |
| `illustrious-xl-v2.0` | **Default.** Newest open Illustrious checkpoint. The card calls this upload "v2.0-STABLE", the more stable checkpoint of the last training phase |
| `illustrious-xl-v1.0` | The model named in the brief. Its card states native 1536 x 1536 |
| `noobai-xl-1.1` | Illustrious derivative trained further on Danbooru and e621 data, with stronger recognition of characters and styles. Its card gives recommended settings |

Only the single-file checkpoint of NoobAI-XL is downloaded. The repository's diffusers folders are not needed.

Generation defaults per profile are in [Image generation](image-generation.md#default-parameters). The render flow is the same for all of them ([ADR 0026](decisions/0026-two-pass-image-rendering.md)).

## Defaults and how they can change

| Tier | Text | Image | Download at setup |
| --- | --- | --- | --- |
| Target, Docker sees at least 20 GiB | `gemma-4-26b-a4b` | `illustrious-xl-v2.0` | 21.38 GB |
| Reduced | `gemma-4-12b` | `illustrious-xl-v2.0` | 13.91 GB |

Fixed rules:

1. P02 measures the target default with the [speed rule](gpu-orchestration.md#measuring-and-tuning). If it fails, `gemma-4-12b` becomes the default on every tier and the 26B profiles stay selectable with a speed note.
2. P04 runs the [Italian quality protocol](prompting.md#italian-quality-protocol) on the default, `orion-26b-a4b` and `rocinante-x-12b`. If the default misses a target and another profile meets all of them, that profile becomes the default through a new ADR. Among several passing profiles, the higher naturalness score wins.
3. P04 runs the [consistency protocol](consistency-engine.md#consistency-protocol) and the [extraction evaluation](consistency-engine.md#extraction-evaluation) on every catalog text profile. After one tuning round, a profile that misses a hard target leaves the catalog through a new ADR, and its file can still be registered as a custom profile. The default must pass the Italian protocol, the consistency protocol and the extraction evaluation ([ADR 0034](decisions/0034-character-consistency-protocol-and-verified-profiles.md)).
4. P05 runs the [image grid](image-generation.md#default-parameters) on all three image profiles. The default changes only if another profile has fewer visible defects in the owner's blind review.

Lyrveil never changes the active profile by itself, not even after a failure. A failure shows its reason and the profiles that would fit.

## Catalog file

`config/model-catalog.json` is part of the repository and is reviewed like code. The backend validates it at startup with a Pydantic model and refuses to start on an error. `scripts\lyrveil.cmd models` reads it with the Python standard library. Delivered in P02.1a: the file, with all eight
profiles, and the validator `lyrveil.catalog` in the backend (`load_catalog`, `parse_catalog`). Wiring the validator
into the backend's startup is planned for the settings and model services of P02. One text entry and one image entry:

```json
{
  "schema_version": 1,
  "text": [
    {
      "id": "gemma-4-26b-a4b",
      "display_name": "Gemma 4 26B-A4B",
      "file": "gemma-4-26B_q4_0-it.gguf",
      "url": "https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf/resolve/d1c082be9cf3c8a514acf63b8761f4b41935842e/gemma-4-26B_q4_0-it.gguf",
      "bytes": 14439363584,
      "sha256": "3eca3b8f6d7baf218a7dd6bba5fb59a56ee25fe2d567b6f5f589b4f697eca51d",
      "license": "Apache-2.0",
      "license_url": "https://ai.google.dev/gemma/docs/gemma_4_license",
      "template": "gemma4",
      "min_tier": "target",
      "default_tiers": ["target"],
      "context_size": 16384,
      "moe": true,
      "vram_required_mib": 7000,
      "vram_preferred_mib": 10000,
      "sampler": "gemma4",
      "use_mmap": false,
      "kv_quant": "f16",
      "batch_size": 512,
      "swa_padding": 0,
      "smart_cache": 0,
      "quality_evidence": null
    }
  ],
  "image": [
    {
      "id": "illustrious-xl-v2.0",
      "display_name": "Illustrious XL v2.0",
      "file": "Illustrious-XL-v2.0.safetensors",
      "url": "https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0/resolve/69459c1fe6f46db41ab31e6114f05acc0e06bcaa/Illustrious-XL-v2.0.safetensors",
      "bytes": 6938040674,
      "sha256": "c2a1a3eaa13d4c107dc7e00c3fe830cab427aa026362740ea094745b3422a331",
      "license": "CreativeML-OpenRAIL-M",
      "license_url": "https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0",
      "architecture": "sdxl",
      "prediction": "epsilon",
      "defaults": "illustrious",
      "default_tiers": ["reduced", "target"]
    }
  ]
}
```

Rules checked by the validator:

- Identifiers are unique and match `^[a-z0-9][a-z0-9.-]{1,47}$`. File names match `^[A-Za-z0-9._-]{1,128}\.(gguf|safetensors)$`.
- Every URL is `https://huggingface.co/<repo>/resolve/<40-hex revision>/<file>`. Moving branches are refused.
- `template` is one of `gemma4`, `mistral_v3_tekken`, `chatml`. `prediction` is `epsilon` in 1.0.
- `sampler` and `defaults` name presets defined in code, so the catalog cannot carry arbitrary parameters.
- `sampler` is `gemma4`, `nemo` or `generic`; `defaults` is `illustrious` or `noobai`. A custom profile always gets `generic` (`custom_sampler_preset`).
- `swa_padding` is 0, 1024 or 2048, and above 0 only for a `gemma4` profile. `smart_cache` is 0 or 2, and 2 only for a target-tier profile.
- `quality_evidence` is null or a path under `docs/verification/`. The P08 release check fails while a default profile has none.
- Exactly one default text profile per tier and one default image profile exist. `default_tiers` lists the tiers
  (`reduced`, `target`) on which a profile is the default, and is empty or absent for the others; the default image
  profile lists both tiers. `min_tier` is absent for a profile that runs on every tier, as for every image profile.

`scripts\lyrveil.cmd models` (P01.6c) reads only `id`, `display_name`, `file`, `url`, `bytes`, `sha256`, `license`,
`license_url`, `min_tier` and `default_tiers`, and refuses a catalog that breaks the identifier, file name, HTTPS,
size, hash or default rules. It ignores the other fields, which the backend's validator checks.

## Selecting a profile

**Impostazioni > Modelli** lists each profile with its state: present and verified, present with a size mismatch, or missing. A missing file shows its size and the exact command, for example `.\scripts\lyrveil.cmd models -Model orion-26b-a4b`. Profiles above the machine's tier are listed but cannot be selected. A text profile whose `quality_evidence` is set shows **Qualità verificata**, with a link to the summary of its results.

| Change | Effect |
| --- | --- |
| Text profile | The backend writes `MODEL_PROFILE`, `MODEL_FILE`, `CONTEXT_SIZE`, `MOE_CPU_LAYERS`, `USE_MMAP`, `KV_QUANT`, `SWA_PADDING` and `SMART_CACHE` to `runtime/llm-launch.env` in the data volume. **Applica ora** waits for the running reply, sets the desired mode to `none` and then to `llm`, so the runtime stops, the release is verified and the new file loads. Queued work continues with the new model |
| Image profile | Stored in `images.profile_id`. The next image job uses it. ComfyUI loads checkpoints on demand, so no restart is needed |
| Quality preset | `images.quality` sets the default. The image form can override it per request |

A text profile change also:

- invalidates cached token counts, because `messages.tokenizer_id` records the profile;
- makes the next reply process the full prompt once;
- leaves memories, summaries and characters untouched, because they are stored as text.

## Custom profiles

A user can register a file imported into the models volume with `.\scripts\lyrveil.cmd import-model <file>`:

1. **Aggiungi file locale** lists unregistered `.gguf` and `.safetensors` files in `llm/` and `image/checkpoints/` of the volume. Paths outside them cannot be chosen.
2. For a GGUF file the user picks the template family. For a checkpoint, only SDXL with epsilon prediction is offered.
3. The backend computes the SHA-256 in a background job and stores the profile in the setting `models.custom` as `custom-<first 12 hex characters of the hash>`.
4. The profile is marked **non verificato**. It uses the reduced-tier VRAM thresholds of its family and the [`generic` sampler preset](prompting.md#sampler-presets). A size change makes it invalid until it is registered again.

The anchor, the length bands and the reply normalizer also apply to custom profiles, but no protocol has measured them, so
the interface never shows **Qualità verificata** for them.

Lyrveil never downloads, converts or edits custom files. Loading a model file runs complex parsing code in the runtime, so custom files should come from sources the user trusts. See [Security](security.md#model-files).

### Delivered in P06.6a

The backend side of the two sections above, in `backend/src/lyrveil/services/model_profiles.py`,
`services/model_maintenance.py`, `model_files.py`, `orchestration/launch_file.py` and the routes of
`api/model_settings.py` ([API](api.md#jobs-scheduling-notifications-settings-data),
[ADR 0042](decisions/0042-profile-reload-as-a-queued-job-and-name-only-registration.md)). The screens are P06.6b.

- **Tier.** The backend reads `MemTotal` of `/proc/meminfo` in its container, the memory of the Docker VM: from 20 GiB
  the machine is on the target tier, otherwise, and when the file cannot be read, on the reduced tier, as
  `scripts\lyrveil.cmd models` decides.
- **Local state.** `verified` means the file is present with the catalog's size, or the size it was registered at;
  the SHA-256 of a catalog file is checked by `scripts\lyrveil.cmd models -Verify`, not by the backend.
- **Text profile change.** `PATCH /settings/models` checks the profile first: it must exist, run on the tier, and have
  its file with the expected size, or it answers `MODEL_PROFILE_UNAVAILABLE` and changes nothing. It then stores
  `llm.profile_id` and writes the whole launch file at level A in one transaction. Selecting the active profile again
  returns it to level A. When no launch file exists, a backend start writes the active profile's.
- **Applica ora.** `POST /settings/models/apply` checks the launch file's model file the same way, then queues a
  `maintenance` job (`model_reload`) at priority 5, ahead of replies, or returns the one already queued. The worker
  runs one job at a time, so the reload starts once the running reply has ended. It checks the file again
  (`MODEL_FILE_MISSING`, no mode change), sets the desired mode to `none`, waits up to 180 s for IDLE, which the
  orchestrator enters only after the release is verified, then sets `llm`. The job ends there; the load is followed in
  the GPU state. A wait that ends in ERROR or runs out fails with `GPU_RELEASE_UNVERIFIED`, after `llm` was asked for
  again.
- **Custom profiles.** `POST /settings/models/custom` takes a kind, a file name and, for a `.gguf`, the template
  family ([Security](security.md#model-files) lists the checks). A `maintenance` job (`custom_model_hash`) hashes the
  file and stores `{id, kind, file, bytes, sha256, template, registered_at}` in `models.custom`. The same content
  registered under another name keeps its first entry. A file registered again after a size change replaces its old
  entry unless that one is active. A custom text profile takes the context, batch and cache type of the reduced-tier
  catalog profile of its family (`gemma-4-12b`, `rocinante-x-12b`, `rocinante-12b-v1.1`), with `USE_MMAP`,
  `SWA_PADDING` and `SMART_CACHE` at 0. Launch levels never change a custom profile's file.
- **Not delivered.** Choosing the image profile and the quality preset (`images.profile_id` and `images.quality` are
  not read or written). Replies, drafts, memories and summaries still use the `gemma4` template and sampler preset
  whatever the active profile, so a `mistral_v3_tekken` or `chatml` profile, or a custom profile, is not yet prompted
  in its own format. A hash runs in the job worker, so other jobs wait for it. Cached token counts are keyed by the
  loaded model's name, so they follow a change by themselves.
- **Tests.** T-MOD-03 and T-MOD-04 in `backend/tests/integration/test_model_profiles.py`, with the orchestrator's side
  of T-MOD-03 in `orchestrator/tests/integration/test_profile_change.py`. Nothing has run against the real runtime or
  GPU.

## Memory estimates

Planning figures for the reference card with 10,600 MiB free at idle. P02 and P05 replace them with measurements.

| Profile | Weights | KV cache at the default context | Compute and output buffers | On the GPU | In system RAM |
| --- | --- | --- | --- | --- | --- |
| `gemma-4-26b-a4b` | 13,770 MiB | About 620 MiB at 16384 | 600 to 1,000 MiB | About 9,000 to 9,600 MiB, set by AutoFit | About 5,000 to 6,500 MiB of expert weights |
| `gemma-4-12b` | 6,653 MiB | About 740 MiB at 16384 | 600 to 1,000 MiB | About 8,000 to 8,400 MiB | Little |
| `orion-26b-a4b` | 17,134 MiB | About 620 MiB at 16384 | 600 to 1,000 MiB | About 9,000 to 9,600 MiB, set by AutoFit | About 8,500 to 10,000 MiB of expert weights |
| `rocinante-x-12b`, `rocinante-12b-v1.1` | 7,131 MiB | 1,280 MiB at 8192 | 300 to 600 MiB | About 8,700 to 9,000 MiB | Little |

How the KV figures were computed, with 16-bit cache entries:

- **Gemma 4 26B-A4B.** 25 sliding-window layers keep about 1,536 positions each (a 1,024 window plus a batch), with 8 key-value heads of size 256. 5 global layers keep the full context, with 2 key-value heads of size 512.
- **Gemma 4 12B.** 40 sliding layers with 8 heads of size 256, and 8 global layers with 1 head of size 512.
- **Mistral Nemo finetunes.** 40 layers with 8 heads of size 128.

The large Gemma vocabulary of 262,144 tokens makes the output buffer bigger than Mistral Nemo's.

Measured levers of [ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md), with the same arithmetic:

- **SWA padding.** Each 1,024 extra positions cost about 200 MiB of VRAM for Gemma 4 26B-A4B and about 320 MiB for Gemma 4 12B.
- **SmartCache.** Each slot holds a KV snapshot in RAM, about the size of the KV cache in the table above.

For the mixture-of-experts profiles, KoboldCpp's AutoFit decides which expert weights stay in RAM. With `--gpulayers -1` and no `--moecpu`, the launcher enables AutoFit with 1,024 MB of padding (`koboldcpp.py` at `v1.124.2`). `MOE_CPU_LAYERS` sets `--moecpu` explicitly, which disables AutoFit. P02 uses it only if AutoFit fails or is slower.

Weights kept in RAM are copied into the runtime's memory by default, while the file also fills the page cache.
`USE_MMAP=1` maps them from the page cache instead, which can save their size in RAM and keep both default files
cached within the 24 GB given to Docker. P02 decides per profile ([ADR 0030](decisions/0030-koboldcpp-efficiency-levers.md)).

Expected speed, also an estimate: each token reads about 0.8 GB of active expert weights. With about 40 to 45 % of them in RAM, dual-channel DDR5-5600 allows well over 15 tokens per second. Processing a new long prompt is slower than with a dense model that fits on the GPU, because expert weights move over PCIe for each batch.
