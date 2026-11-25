# Model catalog

Status: accepted (2026-11-05), revised 2026-11-06 for verified-profile evidence and cache levers, and 2026-11-25 to drop the Gemma 4 profiles and make `rocinante-x-12b` the default ([ADR 0045](decisions/0045-drop-gemma-4-rocinante-x-default.md)). Implementation: the catalog file and its validator are delivered (P02.1a), and so are the backend's text profile change and custom registration (P06.6a, [Delivered in P06.6a](#delivered-in-p066a)); the **Modelli** screen and the `models` command are delivered too (P06.6b, P01.6c). Not delivered: the image profile and quality choice, and the prompt template and sampler preset following the active text profile (see the "Not delivered" note under [Delivered in P06.6a](#delivered-in-p066a)). The P02.7 tuning procedure ran on the reference machine on 2026-11-25 ([evidence](verification/2026-11-25-p02-7-vram-tuning.md)); the measurements of P04.10 and P05.8 have not run. Facts were read from the Hugging Face API, the model cards and the official chat templates on 2026-11-05. GPU.2 and GPU.3 loaded the text profiles on the reference machine on 2026-11-25; figures marked as measured come from them, the others are estimates.

The user chooses the active text and image models from verified profiles ([ADR 0024](decisions/0024-selectable-model-profiles.md)). One text model serves every text task, and it never shares the GPU with the image model ([ADR 0005](decisions/0005-exclusive-gpu-by-container-lifecycle.md)).

## Text profiles

| Profile | File | Source, pinned revision | Bytes | SHA-256 | License | Template | Tier |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `rocinante-x-12b` | `TheDrummer_Rocinante-X-12B-v1-Q4_K_M.gguf` | `bartowski/TheDrummer_Rocinante-X-12B-v1-GGUF` at `4302599a9fecd0e250c3790d77a38f3bd791fdd4` | 7,477,207,712 | `1ca25853a19d6b15f9a94f7e5bc86f4cc88bd0c9dea12a00ea55452b4208ade6` | Not stated | `mistral_v3_tekken` | Reduced |
| `rocinante-12b-v1.1` | `Rocinante-12B-v1.1-Q4_K_M.gguf` | `bartowski/Rocinante-12B-v1.1-GGUF` at `9d15ca24c675a428fc5821cccc537f2a6f64987d` | 7,477,203,712 | `5133341741709aafcbc162c8fe638143e466288b7942ba7a0e9d9ef4e08d056f` | `other`, no terms | `chatml` | Reduced |

"Tier" is the smallest [hardware tier](decisions/0027-hardware-tiers-after-ram-upgrade.md) that runs the profile. A profile for the reduced tier also runs on the target tier. No repository is gated, and none needs an account.

| Profile | Base | What it is for |
| --- | --- | --- |
| `rocinante-x-12b` | Mistral Nemo Instruct 2407, roleplay finetune by TheDrummer, dense | **Default on both tiers** ([ADR 0045](decisions/0045-drop-gemma-4-rocinante-x-default.md)). Successor of the brief's model. Fits entirely in VRAM. Mistral Nemo lists Italian among its languages |
| `rocinante-12b-v1.1` | Mistral Nemo Base 2407, roleplay finetune, dense | The model named in the brief, kept for continuity |

The Gemma 4 profiles `gemma-4-26b-a4b` and `gemma-4-12b`, and their finetune `orion-26b-a4b`, left the catalog on 2026-11-25 by an owner decision ([ADR 0045](decisions/0045-drop-gemma-4-rocinante-x-default.md)). Their files can still be registered as [custom profiles](#custom-profiles) with the `gemma4` template family.

The finetune cards do not list Italian by name. Italian quality is measured with the [protocol](prompting.md#italian-quality-protocol) for both profiles.

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
| Target, Docker sees at least 20 GiB | `rocinante-x-12b` | `illustrious-xl-v2.0` | 14.42 GB |
| Reduced | `rocinante-x-12b` | `illustrious-xl-v2.0` | 14.42 GB |

Owner decision 2026-11-25: drop Gemma 4, `rocinante-x-12b` default ([ADR 0045](decisions/0045-drop-gemma-4-rocinante-x-default.md)). Its launch values are the GPU.3 decisions: level A, context 12288, batch 512, `MOE_CPU_LAYERS=0`, `USE_MMAP=0`, `KV_QUANT=f16`, with about 2.2 GiB of margin at 12288 on the RTX 4070 ([evidence](verification/2026-11-25-gpu-tuning.md)). `rocinante-12b-v1.1` has the same architecture and takes the same values; T-GPU-02 measured both at them on 2026-11-25, with about 2210 MiB of margin ([evidence](verification/2026-11-25-rocinante-default-gpu.md)).

Fixed rules:

1. P02 measures the default with the [speed rule](gpu-orchestration.md#measuring-and-tuning). Applied in P02.7 on the reference machine on 2026-11-25 for `rocinante-x-12b` at its catalog values, context 12288 (T-GPU-14): a median of 53.1 tokens per second over 20 replies and a first token in 2.22 s for a 6087-token prompt, so it stays the default on both tiers ([evidence](verification/2026-11-25-p02-7-vram-tuning.md); the DROP.1 run of the same day measured 53.0 and 2.22 s, [evidence](verification/2026-11-25-rocinante-default-gpu.md)). It is a dense model that fits in VRAM, so the rule's fallback for a mixture-of-experts default does not apply.
2. P04 runs the [Italian quality protocol](prompting.md#italian-quality-protocol) on both text profiles. If the default misses a target and the other profile meets all of them, that profile becomes the default through a new ADR.
3. P04 runs the [consistency protocol](consistency-engine.md#consistency-protocol) and the [extraction evaluation](consistency-engine.md#extraction-evaluation) on every catalog text profile. After one tuning round, a profile that misses a hard target leaves the catalog through a new ADR, and its file can still be registered as a custom profile. The default must pass the Italian protocol, the consistency protocol and the extraction evaluation ([ADR 0034](decisions/0034-character-consistency-protocol-and-verified-profiles.md)).
4. P05 runs the [image grid](image-generation.md#default-parameters) on all three image profiles. The default changes only if another profile has fewer visible defects in the owner's blind review.

Lyrveil never changes the active profile by itself, not even after a failure. A failure shows its reason and the profiles that would fit.

## Catalog file

`config/model-catalog.json` is part of the repository and is reviewed like code. The backend validates it at startup with a Pydantic model and refuses to start on an error. `scripts\lyrveil.cmd models` reads it with the Python standard library. Delivered in P02.1a: the file and the validator `lyrveil.catalog` in the backend (`load_catalog`, `parse_catalog`).
Since [ADR 0045](decisions/0045-drop-gemma-4-rocinante-x-default.md) the file holds five profiles: the two text
profiles and the three image profiles. The backend's startup
loads the catalog through the validator (`main.py`) and refuses to start on an error. One text entry and one image entry:

```json
{
  "schema_version": 1,
  "text": [
    {
      "id": "rocinante-x-12b",
      "display_name": "Rocinante X 12B",
      "file": "TheDrummer_Rocinante-X-12B-v1-Q4_K_M.gguf",
      "url": "https://huggingface.co/bartowski/TheDrummer_Rocinante-X-12B-v1-GGUF/resolve/4302599a9fecd0e250c3790d77a38f3bd791fdd4/TheDrummer_Rocinante-X-12B-v1-Q4_K_M.gguf",
      "bytes": 7477207712,
      "sha256": "1ca25853a19d6b15f9a94f7e5bc86f4cc88bd0c9dea12a00ea55452b4208ade6",
      "license": "Not stated",
      "license_url": "https://huggingface.co/bartowski/TheDrummer_Rocinante-X-12B-v1-GGUF",
      "template": "mistral_v3_tekken",
      "min_tier": "reduced",
      "default_tiers": ["reduced", "target"],
      "context_size": 12288,
      "moe": false,
      "vram_required_mib": 6000,
      "vram_preferred_mib": 9678,
      "sampler": "nemo",
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
- `template` is one of `gemma4`, `mistral_v3_tekken`, `chatml`. No shipped profile uses `gemma4`; it stays for custom profiles. `prediction` is `epsilon` in 1.0.
- `sampler` and `defaults` name presets defined in code, so the catalog cannot carry arbitrary parameters.
- `sampler` is `nemo` or `generic`; `defaults` is `illustrious` or `noobai`. A custom profile always gets `generic` (`custom_sampler_preset`). The `gemma4` preset was removed with the Gemma 4 profiles.
- `swa_padding` is 0, 1024 or 2048, and above 0 only for a `gemma4` profile, so 0 for every shipped profile. `smart_cache` is 0 or 2, and 2 only for a target-tier profile.
- `quality_evidence` is null or a path under `docs/verification/`. The P08 release check fails while a default profile has none.
- Exactly one default text profile per tier and one default image profile exist. `default_tiers` lists the tiers
  (`reduced`, `target`) on which a profile is the default, and is empty or absent for the others; the default image
  profile lists both tiers. `min_tier` is absent for a profile that runs on every tier, as for every image profile.

`scripts\lyrveil.cmd models` (P01.6c) reads only `id`, `display_name`, `file`, `url`, `bytes`, `sha256`, `license`,
`license_url`, `min_tier` and `default_tiers`, and refuses a catalog that breaks the identifier, file name, HTTPS,
size, hash or default rules. It ignores the other fields, which the backend's validator checks.

## Selecting a profile

**Impostazioni > Modelli** lists each profile with its state: present and verified, present with a size mismatch, or missing. A missing file shows its size and the exact command, for example `.\scripts\lyrveil.cmd models -Model rocinante-12b-v1.1`. Profiles above the machine's tier are listed but cannot be selected. A text profile whose `quality_evidence` is set shows **Qualità verificata**, with a link to the summary of its results.

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
  catalog profile of its family (`rocinante-x-12b` for `mistral_v3_tekken`, `rocinante-12b-v1.1` for `chatml`), with
  `USE_MMAP`, `SWA_PADDING` and `SMART_CACHE` at 0. A `gemma4` custom profile has no catalog profile of its family
  since ADR 0045, so it takes the fixed fallback of `services/model_profiles.py`: context 8192, batch 512, `f16`.
  Launch levels never change a custom profile's file.
- **Not delivered.** Choosing the image profile and the quality preset (`images.profile_id` and `images.quality` are
  not read or written). Replies, drafts, memories and summaries use the `mistral_v3_tekken` template and the `nemo`
  sampler preset of the default profile whatever the active profile (ADR 0045), so a `chatml` profile, or a custom
  profile of another family, is not yet prompted in its own format. A hash runs in the job worker, so other jobs wait for it. Cached token counts are keyed by the
  loaded model's name, so they follow a change by themselves.
- **Tests.** T-MOD-03 and T-MOD-04 in `backend/tests/integration/test_model_profiles.py`, with the orchestrator's side
  of T-MOD-03 in `orchestrator/tests/integration/test_profile_change.py`. Nothing has run against the real runtime or
  GPU.

## Memory estimates

Planning figures for the reference card with 10,600 MiB free at idle, with the measurements of GPU.3 beside them.

| Profile | Weights | KV cache at the default context | Compute and output buffers | On the GPU | In system RAM |
| --- | --- | --- | --- | --- | --- |
| `rocinante-x-12b`, `rocinante-12b-v1.1` | 7,131 MiB | 1,920 MiB at 12288 (1,280 MiB at 8192) | 300 to 600 MiB | About 9,200 MiB at 12288, measured for both | About 1,100 to 1,400 MiB measured |

The KV figure is computed with 16-bit cache entries: 40 layers with 8 key-value heads of size 128, so 160 KiB per
position. Measured on 2026-11-25 for `rocinante-x-12b` with about 850 MiB in use by other programs: a card peak of 9,412 MiB
at 8192, and of 10,056 MiB at 12288, a margin of 2,226 MiB ([evidence](verification/2026-11-25-gpu-tuning.md)). P02.7
measured a peak of 10,058 MiB at 12288 over a baseline of 841 MiB, a footprint of 9,217 MiB; plus 5 % it gives the
catalog's `vram_preferred_mib` of 9,678 for both Rocinante profiles. `vram_required_mib` keeps the planning value
6,000, because no load with fewer layers on the GPU was measured
([evidence](verification/2026-11-25-p02-7-vram-tuning.md)).

- **SWA padding** ([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)) serves only the
  sliding-window layers of the `gemma4` family, so it does not apply to the shipped profiles.
- **SmartCache.** Each slot holds a KV snapshot in RAM, about the size of the KV cache in the table above.
- **`MOE_CPU_LAYERS`** serves only a mixture-of-experts profile. Both shipped profiles are dense, so it stays 0.

Weights kept in RAM are copied into the runtime's memory by default, while the file also fills the page cache.
`USE_MMAP=1` maps them from the page cache instead. Both profiles fit in VRAM, and GPU.3 measured no RAM saving for
`rocinante-x-12b` (-54 MiB), so `USE_MMAP` stays 0 ([ADR 0030](decisions/0030-koboldcpp-efficiency-levers.md)).
