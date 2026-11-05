# ADR 0030: KoboldCpp efficiency levers measured in P02

Status: accepted (2026-11-05). Supersedes: none.

## Context
On 2026-11-05 the owner asked to check that every decision is the most efficient one. `koboldcpp.py` at the pinned commit `6d2fedf8624e5f05926af3191f9efdcb13c4133c` offers three levers that the design did not use:

- `--usemmap` loads the model with mmap. It is off by default. Without it, weights kept in RAM are copied into the process, while the file also fills the page cache. With 24 GB for Docker, both the 14.44 GB text file and the 6.94 GB image file stay cached only if that copy is avoided.
- `--batchsize` accepts -1, 16, 32, 64, 128, 256, 512, 1024, 2048 and 4096. With expert weights in RAM, a larger batch can process long prompts faster, which matters for the first-token rule of [ADR 0024](0024-selectable-model-profiles.md).
- `--quantkv` accepts `f16`, `bf16`, `q8_0`, `q5_1` and `q4_0`. Flash Attention is on by default at this version, so `q8_0` halves the KV cache. That frees about 300 MiB for Gemma 4 26B-A4B at 16384 tokens, and about 640 MiB for the Mistral Nemo profiles at 8192.

`--moe-cache-mib` also exists, but the source marks it experimental.

## Decision
- Two launch keys are added: `USE_MMAP` (`0` or `1`, default `0`) and `KV_QUANT` (`f16` or `q8_0`, default `f16`). `BATCH_SIZE` also accepts `2048`. The catalog stores the chosen values per profile.
- P02 measures each lever for the measured profiles, with these rules:
  - **mmap**: `1` for a profile if it saves at least 2 GiB of RAM or 20 % of the reload time after an image job, and costs at most 5 % of generation speed.
  - **Batch**: 256, 512, 1024 and 2048 are compared. 512 stays unless another value gains at least 20 % of prompt speed and the VRAM margin rule still holds.
  - **KV cache**: `q8_0` for a profile only if it frees at least 256 MiB or allows the next context step, costs at most 5 % of speed, and the P04 Italian protocol scores no lower. Until P04, `f16` stays.
- `--moe-cache-mib` is not used.

## Alternatives
- Turning the levers on by default now: each one has a cost that only a measurement shows, such as page-cache eviction with mmap, a larger compute buffer with batch 2048, or a quality loss with a quantized cache.
- `q4_0` for the KV cache: a larger saving, but a known quality risk for long contexts.
- Leaving the levers out: cheap to document, but they are the largest efficiency gains left on this hardware.

## Consequences
Launch files, the entrypoint, the catalog and the P02 procedure gain these keys. The defaults keep upstream behavior until the measurements decide.
