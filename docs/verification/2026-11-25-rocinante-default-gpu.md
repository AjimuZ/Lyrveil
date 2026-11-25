# The two Rocinante profiles on the GPU after dropping Gemma 4, 2026-11-25

Status: recorded 2026-11-25 for DROP.1 ([ADR 0045](../decisions/0045-drop-gemma-4-rocinante-x-default.md)). The `gpu`
level ran against the new catalog (two text profiles, `rocinante-x-12b` the default of both tiers at context 12288) on
the reference machine. T-GPU-02 to T-GPU-04 recorded no problem for either profile, and the tuning procedure, version
2, made every decision for `rocinante-x-12b` with T-GPU-14 passing. No single run covered the whole level.

## What ran

From Git Bash in the repository root, on the NVIDIA GeForce RTX 4070 (12282 MiB, 841 to 859 MiB in use by other
programs before each load), KoboldCpp 1.124.2, Pester 5.7.1:

```text
scripts/lyrveil.cmd test gpu
scripts/lyrveil.cmd test gpu rocinante-x-12b/A rocinante-x-12b/context-16384
scripts/lyrveil.cmd test gpu rocinante-x-12b/batch-* rocinante-x-12b/kv-*
scripts/lyrveil.cmd test gpu rocinante-x-12b/mmap-*
```

- The first command, the whole level, was stopped by the 10-minute limit of a tool call here while it was in the
  tuning file, after `tests/gpu/TextProfiles.Tests.ps1` had finished. Its console output was lost with it, so its
  Pester summary, and the T-GPU-01 result of `Passthrough.Tests.ps1`, were not seen. Its text-profile results file,
  `test-results/gpu/text-profiles-20261125-214343.json`, was written. The next command removed the test container it
  left, in its first step.
- The three selector runs measured every trial of the version-2 plan. Each ended with `test gpu: passed` and
  `no container or volume of the test project is left`. Results files (ignored by Git):
  `tuning-20261125-215311.json`, `-215617` and `-220021`; the last one holds every decision and the source of each
  trial.

## T-GPU-02 to T-GPU-04

From `text-profiles-20261125-214343.json`. Both profiles at the catalog's level A: context 12288, batch 512,
`GPU_LAYERS=-1`, `USE_MMAP=0`, `KV_QUANT=f16`.

| Profile | Ready | Loaded / peak | Margin | RAM | 3 generations | Tokenization | Stream | Abort |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `rocinante-x-12b` | 33 s | 10068 / 10070 MiB | 2212 MiB | 1372 MiB | 40.9, 53.7, 53.4 T/s | 170 ids, equal to `mistral_v3_tekken.tokens.json` | `stop` after 34 tokens, 20 events | ended 0.28 s after the abort, 32 tokens |
| `rocinante-12b-v1.1` | 29.1 s | 10068 / 10072 MiB | 2210 MiB | 1065 MiB | 50.3, 53.2, 53.1 T/s | 230 ids, equal to `chatml.tokens.json` | `stop` after 34 tokens, 16 events | ended 0.28 s after the abort, 32 tokens |

Every `problems` list in the file is empty, and every number the file's assertions read meets them: the runtime
served the file at 12288 tokens, VRAM grew by more than 1 GiB, 3 of 3 generations answered, the margin is above
512 MiB (A-01), and no control text reached a reply. This is read from the results file; the Pester lines of that
run were not seen. `rocinante-12b-v1.1` was measured here for the first time at the values it reuses from
`rocinante-x-12b`, with the same margin.

## Tuning procedure, version 2, for `rocinante-x-12b`

The console lines of kv-q8_0, mmap-0 and the batch trials were cut from the output kept here; their figures below come
from the decisions of `tuning-20261125-220021.json`.

| Trial | Ready | Peak | Margin | Numbers |
| --- | --- | --- | --- | --- |
| A (12288, batch 512) | 29 s | 10058 MiB | 2224 MiB | median 53.0 T/s over 20 replies, first token 2.22 s for 6089 tokens, 2744 T/s |
| context-16384 | 29 s | 10702 MiB | 1580 MiB | median 53.1 T/s over 3 replies, first token 2.12 s |
| batch-256 / 1024 / 2048 | | | 2380 / 1912 / 1280 MiB | prompt speed -3.4 %, +4.7 %, +2.3 % against 512 |
| kv-q8_0-context-16384 | 31 s | 9488 MiB | 2794 MiB | 49.0 T/s over 3 replies; at 12288, kv-q8_0 frees 914 MiB of peak and costs 5.9 % of speed |
| mmap-1 (after an image job) | 31.1 s | 10050 MiB | 2232 MiB | median 52.8 T/s over 5 replies; against mmap-0 it saves 370 MiB of RAM, -0.3 % of the reload, costs 0.4 % of speed |

Decisions computed by `Get-GpuTuningDecision`, none set by hand:

| Step | Decision |
| --- | --- |
| 4, level | A: 2224 MiB of margin |
| 5, context | 16384: both margins at least 1024 MiB |
| 6, batch | 512: no size gains 20 % |
| 7, `MOE_CPU_LAYERS` | 0: not a mixture-of-experts profile |
| 8, `USE_MMAP` | 0: saves under 2 GiB and under 20 % of the reload |
| 9, `KV_QUANT` | `f16`: the speed cost of `q8_0` is over 5 % |
| T-GPU-14 | Passes: 53.0 T/s and 2.22 s at context 12288 |

## Not applied, not run or not verified

- Step 5 now allows a second context step, to 16384, from the 12288 that GPU.3 decided. The catalog stays at 12288,
  the value of the owner's decision and of GPU.3; growing it again is left to the owner, because the procedure's
  rule was written for one step from the profile's first value.
- No run covered the whole level in one go, because it takes longer than the 10-minute limit of a tool call here.
  T-GPU-01 did not report a seen result in this task.
- Each tuning trial changes one lever from level A, so no combination was loaded; the catalog's values are level A
  itself, which trial A and T-GPU-02 loaded.
- The reload time has about 2 s of resolution, and `docker stats` includes the page cache, as in GPU.3.
