# Consistency and image verification, 2026-11-06

Purpose: record the upstream facts and the arithmetic behind ADRs
[0031](../decisions/0031-stable-prefix-dynamic-context-and-anchor.md) to
[0036](../decisions/0036-structured-image-scenes-and-measured-levers.md), and the checks run on the documentation.

## What was not executed

No text model was loaded, no image was rendered and no quality protocol ran. The consistency protocol (T-QLT-05), the
extraction evaluation (T-QLT-06), the image identity protocol (T-QLT-07), the layout comparison and the SWA padding,
SmartCache and PAG measurements are planned for P04, P05 and P08. Every figure below that is not a quotation or a
line reference is an estimate, and the phase named with it replaces it with a measurement.

## Sources

The files were downloaded to a temporary folder outside the repository and searched as text. None of them was run.

| Source | Version | Files read |
| --- | --- | --- |
| [KoboldCpp](https://github.com/LostRuins/koboldcpp) | Commit `6d2fedf8624e5f05926af3191f9efdcb13c4133c`, tag `v1.124.2` | `koboldcpp.py` (14,712 lines), `kcpp_src/gpttype_adapter.cpp` (9,540 lines) |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | Tag `v0.43.0` | `comfy/sd1_clip.py`, `comfy/sdxl_clip.py`, `comfy_extras/nodes_pag.py`, `nodes.py` |
| [NoobAI-XL 1.1](https://huggingface.co/Laxhar/noobai-XL-1.1) card | Revision `814a274af2b8097c0828819d561ec74c7d0c6cea` | `README.md` |

## KoboldCpp

| Fact | Where | Used in |
| --- | --- | --- |
| SWA turns on by itself for a model that has it, unless `--noswa` is set. Context shifting is then turned off. With fast forwarding, the startup message warns that SWA "can lead to degraded recall" | `gpttype_adapter.cpp` lines 3797 to 3815 | ADR 0031, [Runtime contracts](../runtime-contracts.md) |
| When the SWA cache no longer holds the window before the reuse point, the runtime replays `2.0 × window` cached tokens before that point. For sliding (not chunked) SWA its debug message calls the recovery "approximate" | `gpttype_adapter.cpp` lines 8165 to 8205 | ADR 0031, [Memory engine](../memory-engine.md#prefix-stability) |
| `--swapadding` defaults to `swa_padding_default = 0`. Help text: "How much extra to pad the SWA KV cache, this affects the rewind limit before reprocessing is forced". `--noswa` forces the padding to 0 | `koboldcpp.py` lines 73, 2292 and 14595 | ADR 0031, [Configuration](../configuration.md) |
| `--smartcache` takes an optional number. No value or 1 gives `savestate_limit_default = 5` slots, and 2 or more sets the count | `koboldcpp.py` lines 61, 2293 to 2297 and 14591 | ADR 0031, [Runtime contracts](../runtime-contracts.md) |
| SmartCache compares the cached prompt with the new one. Below a similarity of `0.7f`, it saves the cached state to a slot and restores a slot that matches by more than 0.7. Without fast forwarding it turns itself off with a message | `gpttype_adapter.cpp` lines 8039 to 8070, and 3847 to 3850 | ADR 0031 |
| `--noswa` (alias `--swa-full`), `--nofastforward` (which "will also enable noshift"), `--noshift`, and `--smartcontext` ("Outdated. Not recommended.") exist. `--useswa` is deprecated and hidden | `koboldcpp.py` lines 14571, 14564, 14570, 14592 and 14701 | [Runtime contracts](../runtime-contracts.md) |

## ComfyUI

| Fact | Where | Used in |
| --- | --- | --- |
| `(text)` multiplies the weight by 1.1, and `(text:w)` sets it. Weights apply as `(z - z_empty) * weight + z_empty` | `comfy/sd1_clip.py` lines 348 to 366, and 54 to 62 | ADR 0035, [Text encoding](../runtime-contracts.md#text-encoding) |
| `\(` and `\)` are escaped before parsing and restored afterwards | `comfy/sd1_clip.py` lines 368 to 376 | [Sanitizer](../image-generation.md#sanitizer) |
| The `embedding:` prefix loads a file. A missing one logs `warning, embedding:<name> does not exist, ignoring` | `comfy/sd1_clip.py` lines 537 and 594 to 606 | [Sanitizer](../image-generation.md#sanitizer) |
| Chunks hold 77 tokens with the start and end tokens. A word of 8 or more tokens (`max_word_length = 8`) can be split at the boundary, and a shorter one moves whole to the next chunk | `comfy/sd1_clip.py` lines 536 and 625 to 660 | [Caps and prompt chunks](../image-generation.md#caps-and-prompt-chunks) |
| Chunk outputs are joined with `torch.cat(output, dim=-2)`. The pooled vector is taken from the first chunk only | `comfy/sd1_clip.py` lines 46 to 68 | ADR 0035 |
| SDXL joins the CLIP-L and CLIP-G outputs and returns the CLIP-G pooled vector | `comfy/sdxl_clip.py` lines 56 to 62 | ADR 0035 |
| `PerturbedAttentionGuidance` takes `model` and `scale`, default 3.0, from 0 to 100. Scale 0 returns the normal result. Otherwise each step runs one more conditional evaluation with self-attention replaced in the middle block | `comfy_extras/nodes_pag.py` lines 13 to 53 | ADR 0036, [Measured levers](../image-generation.md#measured-levers) |
| `nodes_pag.py` is in the list of built-in extra nodes, so no custom node is needed | `nodes.py` lines 2409 and 2456 | ADR 0036 |

## NoobAI-XL 1.1 card

| Fact | Where | Used in |
| --- | --- | --- |
| CFG 5 to 6, 25 to 30 steps, Euler a | `README.md` lines 39 to 41 | [Image model facts](../runtime-contracts.md#image-model-facts) |
| Sizes 768x1344, 832x1216, 896x1152, 1024x1024, 1152x896, 1216x832, 1344x768 | `README.md` line 42 | ADR 0036 |
| Caption order: subject count, character, series, artists, special tags, general tags, other tags | `README.md` line 64 | ADR 0035 |
| Quality tags by popularity percentile, and date tags up to `newest` | `README.md` lines 77 to 93 | [Image generation](../image-generation.md) |

## Arithmetic

All values use 16-bit cache entries and the architecture figures already recorded in
[Model catalog](../models.md#memory-estimates). They are estimates until P02 and P04 measure them.

| Quantity | Computation | Result |
| --- | --- | --- |
| SWA cache per position, Gemma 4 26B-A4B | 25 layers × 8 heads × 256 × 2 (key and value) × 2 bytes | 204,800 bytes, so 200 MiB per 1,024 positions |
| SWA cache per position, Gemma 4 12B | 40 layers × 8 heads × 256 × 2 × 2 bytes | 327,680 bytes, so 320 MiB per 1,024 positions |
| `--noswa` cost, 26B-A4B at 16384 | (16,384 − 1,536) positions × 204,800 bytes | About 2.8 GiB |
| Rewind without replay, Gemma 4 | The SWA cache holds at most about window + batch + padding positions, 1,536 + padding. A reuse point more than about 512 + padding tokens behind the end of the cache always starts the replay | At most about 512, 1,536 or 2,560 tokens for padding 0, 1024 or 2048. P04 measures the real limit |
| Context targets at 8192 | Blocks 1 to 6 and 9: 350 + 700 + 350 + 200 + 900 + 600 + 110 | 3,210 tokens. With at most 400 for the reply and 64 of margin, about 4,500 remain |
| Tokens processed per reply, full attention | The last exchange plus the new dynamic context and message | About 1,000 to 2,500 |
| Tokens processed per reply, Gemma 4 with padding 0 | The same, plus up to 2 × 1,024 replayed tokens | Up to about 4,500 |
| **Alta** sizes for `auto` | 1.5 × each first-pass size, multiples of 8 | 1248x1824, 1152x2016, 2016x1152 |

## Documentation checks

The checks ran from a temporary script outside the repository on the final state of the documentation, before the
commit that adds this file. Roadmap item P01.8 adds equivalent checks to CI.

| Check | Result |
| --- | --- |
| Relative links and heading anchors in all Markdown files | 614 checked, 0 broken |
| Requirements traced to a design, phase and test | 180 of 180 |
| Acceptance criteria traced | 20 of 20 |
| Tests in the catalog that belong to a roadmap exit | 148 of 148 |
| Test identifiers used anywhere but missing from the catalog | 0 |
| Requirement identifiers used but undefined | 0 |
| JSON samples that parse | 8 of 8 |
| ADRs with all sections and listed in the index | 36 of 36 |
| CRLF line endings, unfinished-work markers | 0 files, 0 found |

The script also reports that 132 of the 148 catalog tests are named in the traceability table. The 16 others, such
as T-SEC-01, already existed at the previous commit, and each one is in a roadmap exit. None of the tests added by
this change is among them.
