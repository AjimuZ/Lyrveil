# Text profiles on the GPU: T-GPU-02 to T-GPU-04, 2026-11-25

Status: recorded 2026-11-25 for GPU.2, two runs of the `gpu` level on the reference machine.

## What ran

Command, from Git Bash in the repository root, twice:

```text
scripts/lyrveil.cmd test gpu
```

- Machine: the reference machine, NVIDIA GeForce RTX 4070, 12282 MiB as read by `nvidia-smi` (950 to 1180 MiB in use
  by other programs during the passing run). Docker Desktop with Linux containers, 23.5 GiB of memory for Docker.
- Pester 5.7.1 under Windows PowerShell 5.1. KoboldCpp 1.124.2, as `/api/extra/version` reported for every profile.
- `tests/gpu/Passthrough.Tests.ps1` (T-GPU-01) ran first, then `tests/gpu/TextProfiles.Tests.ps1`. Every profile ran
  at launch level A of the catalog: the profile's context, `BATCH_SIZE=512`, `GPU_LAYERS=-1` (AutoFit),
  `MOE_CPU_LAYERS=0`, `USE_MMAP=0`, `KV_QUANT=f16`, `SWA_PADDING=0`, `SMART_CACHE=0`.
- Results file of the passing run: `test-results/gpu/text-profiles-20261125-192732.json` (ignored by Git), copied as
  [2026-11-25-gpu-text-profiles.json](2026-11-25-gpu-text-profiles.json).

## Result

First run (19:21): `Pester 5.7.1: 29 passed, 4 failed`, `test gpu: FAILED (gpu)`. The four failures were the abort
check of every profile, and they were a defect of the test: it read the abort answer through a helper that sees object
properties, not hashtable keys, so `success` read as empty. The measurements of that run showed the abort working (the
stream closed 0.28 to 0.33 s after it, with 22 to 32 of 600 tokens). The test was fixed, and the speed derived from
`last_eval` was corrected (see below).

Second run (19:27): `Pester 5.7.1: 33 passed, 0 failed, 0 skipped, 0 not run`, then `test gpu: passed`. Pester
reported 356.55 s for the tests. The tokenizations recorded by the first run were compared, not recorded again, and
matched for every profile.

Cleanup, both runs: `down --volumes --remove-orphans` and `docker volume rm --force lyrveil-gpu-test-data` ran, and the
level reported `no container or volume of the test project is left`.

### T-GPU-02: load, VRAM and RAM, margin rule

Passing run. "Loaded" is NVML right after the health check; "peak" is the highest of 19 to 36 samples taken every
250 ms during 3 generations of 128 tokens; "margin" is total minus peak; RAM is "docker stats" of the container, after
the load / after the generations.

| Profile | Context | Ready | VRAM before | Loaded | Peak | Margin | RAM | Speed (3 generations) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gemma-4-12b` | 16384 | 43.6 s | 950 MiB | 9234 MiB | 9234 MiB | 3048 MiB | 1658 / 1663 MiB | 45.0, 47.8, 45.9 T/s |
| `gemma-4-26b-a4b` | 16384 | 74.2 s | 1178 MiB | 11240 MiB | 11397 MiB | 885 MiB | 6623 / 6629 MiB | 41.8, 35.8, 41.0 T/s |
| `rocinante-x-12b` | 8192 | 46.9 s | 972 MiB | 9559 MiB | 9859 MiB | 2423 MiB | 1102 / 1107 MiB | 46.9, 48.3, 50.8 T/s |

Every profile became healthy, `/api/v1/model` named its file and `/api/extra/true_max_context_length` was its catalog
context. The margin rule (steps 3 to 5 of [Measuring and tuning](../gpu-orchestration.md#measuring-and-tuning)):

- `gemma-4-12b` and `rocinante-x-12b`: margin of at least 1024 MiB, so level A stays and the context may grow one
  step, but only if a load at the larger context keeps 1024 MiB. That load was not run, so the catalog is unchanged.
- `gemma-4-26b-a4b`: 885 MiB, between 512 and 1023 MiB, so level A stays and the context does not grow. A-01 (at
  least 512 MiB) holds. In the first run the same profile had 1037 MiB (peak 11245 MiB, 1050 MiB in use before), so
  its margin sits close to the 1024 MiB line and moves with what other programs hold. AutoFit placed part of the
  experts in RAM: over both runs the container held 6623 to 7557 MiB, against 1102 to 3326 MiB for the dense 12B
  profiles.

The first run read the RAM of `gemma-4-12b` at 3320 MiB, against 1658 MiB in the second. "docker stats" includes the
page cache, so the RAM figures are upper bounds that vary between runs.

### T-GPU-03: tokenization fixtures

The chat prompt of each family in
[`backend/tests/fixtures/tokenization/prompts.json`](../../backend/tests/fixtures/tokenization/prompts.json),
tokenized with `"special": false`:

| Family | Recorded with | Tokens | Control tokens | BOS with `"special": true` |
| --- | --- | --- | --- | --- |
| `gemma4` | `gemma-4-12b` | 191 | `<\|turn>` 105 five times, `<turn\|>` 106 four times, `<\|channel>` 100 and `<channel\|>` 101 once, as in the text | `[2]` |
| `mistral_v3_tekken` | `rocinante-x-12b` | 170 | `[INST]` 3 and `[/INST]` 4 twice, `</s>` 2 once, as in the text | `[1]` |
| `chatml` | `rocinante-12b-v1.1` | 230 | `<\|im_start\|>` and `<\|im_end\|>` are 6 tokens each, ordinary text (ADR 0014) | `[1]` |

`gemma-4-26b-a4b` gave the same 191 identifiers as `gemma-4-12b`. With `"special": true` each tokenization was the
same identifiers after one BOS token. The recordings are `gemma4.tokens.json`, `mistral_v3_tekken.tokens.json` and
`chatml.tokens.json` in the same folder. `backend/tests/unit/test_tokenization_fixtures.py` passed in a targeted run
(`scripts/lyrveil.cmd test unit tests/unit/test_tokenization_fixtures.py`: 7 passed): each fixture prompt is exactly
what the formatter renders, and each recording names its prompt's SHA-256.

This confirms the layouts of [Prompt format](../prompting.md#prompt-format) as tokens. It does not compare them with the
official Jinja templates, and the prompt has no two character turns in a row, so the `mistral_v3_tekken` order noted in
[Backend](../backend.md#prompt-formatter) is still unchecked.

### T-GPU-04: streaming, token counts, abort

| Profile | Events | Finish | `last_token_count` | Counted again | Speed | Abort: tokens | Closed after abort |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gemma-4-12b` | 57 + 1 | `stop` | 66 | 65 | 38.6 T/s | 30 of 600 | 0.35 s |
| `gemma-4-26b-a4b` | 26 + 1 | `stop` | 34 | 33 | 32.9 T/s | 17 of 600 | 0.37 s |
| `rocinante-x-12b` | 22 + 1 | `stop` | 34 | 33 | 43.2 T/s | 35 of 600 | 0.38 s |
| `rocinante-12b-v1.1` | 22 + 1 | `stop` | 31 | 25 | 39.8 T/s | 32 of 600 | 0.30 s |

Every reply closed with one event holding an empty token, held no control text of its family, and was counted within
the tolerance. For `rocinante-12b-v1.1` the 6-token gap is the trimmed text stop `<|im_end|>` (`stop_reason` 2, a stop
sequence); the Gemma and Mistral replies ended on the end token (`stop_reason` 1). Every abort answered `success`
`"true"`, `done` `"true"`.

Facts that the contract did not record:

- An aborted stream closes with `finish_reason` `"length"`, and `/api/extra/perf` then reports `stop_reason` 0. The
  backend's adapter reports such a stream as `aborted` because it sent the abort itself, so this needs no change.
- `last_eval` of `/api/extra/perf` is not seconds: 66 tokens streamed within about 1.4 s while it read 25.9. Read as
  milliseconds per generated token it gives 38.6 T/s, which matches. That reading is inferred from these runs, not from
  the source. The speeds above use it.

## Not run or not verified

- The peak was sampled over 3 generations, not the 20 of the procedure, and no generation used a long prompt, so the
  peak may be higher with a full context.
- Steps 5 (a load at the larger context), 6 (batch sizes), 7 (the speed rule for the MoE default, which needs 20
  replies of at least 200 tokens and a 6000-token prompt), 8 (mmap) and 9 (KV cache type) were not measured.
- The arrival times come from Windows PowerShell reading `docker compose exec` output, so the spread of the tokens is
  a weak sign of streaming; the abort, which stopped each generation after a few tokens because the client had already
  received them, is the stronger one.
- T-GPU-05 to T-GPU-14 have no test in the level yet.
