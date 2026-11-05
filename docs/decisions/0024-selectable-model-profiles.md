# ADR 0024: Selectable model profiles with tier defaults

Status: accepted (2026-11-05). Supersedes: none. Changes R-MOD-01, R-MOD-04 and R-MOD-06 at the owner's request of 2026-11-05.

## Context
The brief fixed one text model, Rocinante 12B v1.1, and one image model, Illustrious XL v1.0. On 2026-11-05 the owner asked to improve the chosen models where possible and to let the user choose them. Facts read from Hugging Face on 2026-11-05:

- Rocinante 12B v1.1 dates from August 2024. Its author has since published Rocinante-X 12B v1 (January 2026) and Orion 26B-A4B v1.1 (September 2026), a roleplay finetune of Gemma 4 26B-A4B. None of these three cards states license terms.
- Google published Gemma 4 in June 2026 under Apache 2.0, with official Q4_0 GGUF files from quantization-aware training. The 12B is dense. The 26B-A4B is a mixture of experts with 25.2B parameters, of which 3.8B are active per token. The card states support for more than 35 languages but does not list them.
- The reference machine now has 32 GB of RAM ([ADR 0027](0027-hardware-tiers-after-ram-upgrade.md)). That makes a mixture-of-experts model practical: KoboldCpp keeps some expert weights in system RAM (`--moecpu`) and the rest on the GPU.
- Illustrious XL v2.0 is the newest open checkpoint from OnomaAI. Its card states the license `creativeml-openrail-m`.

## Decision
- A **model profile** describes one model file: identifier, display name, pinned download URL, size, SHA-256, license, prompt template family for text, generation defaults and the memory tier it needs. Profiles live in `config/model-catalog.json`, which is reviewed like code.
- The user selects one active text profile and one active image profile in **Impostazioni > Modelli**. One text model is loaded at a time and serves every text task. Image profiles never load together with a text model ([ADR 0005](0005-exclusive-gpu-by-container-lifecycle.md)).
- Text profiles in 1.0:

  | Profile | File | Template | Role |
  | --- | --- | --- | --- |
  | `gemma-4-26b-a4b` | `gemma-4-26B_q4_0-it.gguf` | `gemma4` | Default on the target tier |
  | `gemma-4-12b` | `gemma-4-12b-it-qat-q4_0.gguf` | `gemma4` | Default on the reduced tier, and the fast choice |
  | `orion-26b-a4b` | `TheDrummer_Orion-26B-A4B-v1.1-Q4_K_M.gguf` | `gemma4` | Roleplay finetune, target tier only |
  | `rocinante-x-12b` | `TheDrummer_Rocinante-X-12B-v1-Q4_K_M.gguf` | `mistral_v3_tekken` | Roleplay finetune |
  | `rocinante-12b-v1.1` | `Rocinante-12B-v1.1-Q4_K_M.gguf` | `chatml` | The brief's original model |

- Image profiles in 1.0, all SDXL with epsilon prediction:

  | Profile | File | Role |
  | --- | --- | --- |
  | `illustrious-xl-v2.0` | `Illustrious-XL-v2.0.safetensors` | Default |
  | `illustrious-xl-v1.0` | `Illustrious-XL-v1.0.safetensors` | The brief's original model |
  | `noobai-xl-1.1` | `NoobAI-XL-v1.1.safetensors` | Illustrious derivative with wider character knowledge |

- `setup.sh` downloads the defaults for the machine's tier. Other catalog files are downloaded on request with `./scripts/download-models.sh --model <profile>`. Containers have no Internet access, so the application never downloads anything and shows the command instead.
- **Custom files.** The user can register a GGUF or SDXL checkpoint already placed in `models/`. Lyrveil computes its SHA-256, records it as unverified, and requires a template family for text. It never downloads or edits such files.
- **The default is chosen by tier, and confirmed by measurement.** Gemma 4 26B-A4B is the target-tier default because it is the largest model that runs on this hardware at chat speed, its license is clear, and Google publishes its hashes and template. P02 applies the speed rule in [GPU orchestration](../gpu-orchestration.md#measuring-and-tuning). P04 runs the Italian quality protocol on the default and on both roleplay profiles. If the default fails a target and another profile meets it, the best passing profile becomes the default in a new ADR. The owner authorized such changes on 2026-11-05.
- Profile changes are explicit. Lyrveil never switches profiles by itself, also after a failure.

## Alternatives
- Keep Rocinante 12B v1.1: a 2024 model with unstated license terms and no stated Italian support, while newer models fit the same card.
- Orion 26B-A4B as the default: likely the best roleplay writer in the catalog, but its card states no license. It stays one selection away.
- Mistral Small class models with 24B dense parameters: they do not fit in 12 GB, and dense CPU offload is several times slower than offloading experts.
- Free choice of any file with no catalog: no verified template, hash or license, so failures would be hard to explain.
- Downloading from the application: it would need Internet access in a container, which the security model excludes ([ADR 0017](0017-local-only-security-model.md)).

## Consequences
The text runtime gains a profile key in its launch file, and a profile change cycles the runtime through `IDLE` with the existing states. Prompts, stop sequences, sampler presets and VRAM thresholds come from the active profile. Token counts carry the profile identifier, so a model change recounts them. Each image stores its profile and checkpoint hash. The requirements, test catalog and licensing notes list every profile. Supporting another model means adding a profile and its verification, not changing code, unless it needs a new template family.
