# Measuring and tuning on the GPU: T-GPU-14 and steps 1 to 9, 2026-11-25

Status: recorded 2026-11-25 for GPU.3. The `gpu` level measured every trial of the procedure for `gemma-4-12b`,
`gemma-4-26b-a4b` and `rocinante-x-12b` in twelve partial runs on the reference machine. The last run made every decision
from the stored trials, and T-GPU-14 passed. The catalog is unchanged: applying the decisions belongs to P02.7.

## What ran

`tests/gpu/Tuning.Tests.ps1` measures. [`scripts/lib/GpuTuning.psm1`](../../scripts/lib/GpuTuning.psm1) plans the
trials and computes the decisions. Its rules are unit-tested in `tests/scripts/GpuTuning.Tests.ps1`. A full run takes
about an hour, and a tool call here is limited to 10 minutes, so the trials ran in parts with trial selectors, from Git
Bash in the repository root:

```text
scripts/lyrveil.cmd test gpu gemma-4-26b-a4b/context-24576 gemma-4-26b-a4b/batch-256 gemma-4-26b-a4b/batch-1024
scripts/lyrveil.cmd test gpu gemma-4-26b-a4b/batch-2048 gemma-4-26b-a4b/kv-q8_0 gemma-4-26b-a4b/kv-q8_0-context-24576
scripts/lyrveil.cmd test gpu gemma-4-26b-a4b/moe-8
scripts/lyrveil.cmd test gpu gemma-4-26b-a4b/moe-12
scripts/lyrveil.cmd test gpu gemma-4-26b-a4b/moe-16
scripts/lyrveil.cmd test gpu gemma-4-26b-a4b/mmap-0 gemma-4-26b-a4b/mmap-1
```

These six runs (20:39 to 21:09) followed six runs of an earlier session (20:01 to 20:31) that measured the trials of
`gemma-4-12b`, `rocinante-x-12b` and `gemma-4-26b-a4b/A`. Their results files are kept, but their console output was
not, so their Pester summaries are not reported here. Each run reuses the newest stored record of every trial it did
not select, but only from the same procedure version and GPU, and only a record whose status is `measured` or
`not_loaded` with no problem. Each run's decisions name the file of every trial they read.

- Machine: NVIDIA GeForce RTX 4070, 12282 MiB as read by `nvidia-smi`, with 839 to 859 MiB in use by other programs
  before each trial. Docker Desktop with Linux containers. KoboldCpp 1.124.2. Pester 5.7.1 under Windows PowerShell
  5.1.
- Every trial is the catalog's level A with one lever changed and `LEVEL=CUSTOM`. Level A is the profile's context,
  `BATCH_SIZE=512`, `GPU_LAYERS=-1` (AutoFit), `MOE_CPU_LAYERS=0`, `USE_MMAP=0`, `KV_QUANT=f16`, `SWA_PADDING=0` and
  `SMART_CACHE=0`.
- Workload. A reply is the family's structured prompt of `backend/tests/fixtures/tokenization/prompts.json`, with the
  end token banned and no stop sequence. Each reply generates 200 tokens, and its speed is the generated tokens over
  curl's time inside the container. A long prompt is that prompt with a nonce and numbered filler sentences (134 for the
  Gemma 4 profiles), sized by `/api/extra/tokencount` to at least 6000 tokens (6118 to 6122 for `gemma-4-26b-a4b`), and
  answered with one token. Its time is the time to the first token. The VRAM peak
  is sampled every 250 ms during the workload. RAM is `docker stats` of the container after the workload.
- Results files (ignored by Git, on the reference machine): `test-results/gpu/tuning-20261125-200134.json` to
  `tuning-20261125-202609.json` (earlier session), then `tuning-20261125-203956.json`, `-204413`, `-204838`,
  `-205337`, `-205851` and `-210354`. Each holds every trial it ran, with its launch lines, the docker and nvidia-smi
  commands, every reply and prompt, and every VRAM sample count. The last one holds the decisions of all three profiles
  and the source file of each trial.

## Result

| Run | Trials | Pester |
| --- | --- | --- |
| 20:39 | `gemma-4-26b-a4b`: context-24576, batch-256, batch-1024 | 17 passed, 0 failed, 5 skipped |
| 20:44 | `gemma-4-26b-a4b`: batch-2048, kv-q8_0, kv-q8_0-context-24576 | 19 passed, 0 failed, 3 skipped |
| 20:48 | `gemma-4-26b-a4b`: moe-8 | 17 passed, 0 failed, 3 skipped |
| 20:53 | `gemma-4-26b-a4b`: moe-12 | 17 passed, 0 failed, 3 skipped |
| 20:58 | `gemma-4-26b-a4b`: moe-16 | 19 passed, 0 failed, 1 skipped |
| 21:03 | `gemma-4-26b-a4b`: mmap-0, mmap-1 | 22 passed, 0 failed, 0 skipped |

Every run ended with `test gpu: passed` and `no container or volume of the test project is left`. The skips are the
decisions that still lacked a trial in a partial run. Such a test is skipped only when trials were selected, and it
fails in a full run. The last run skipped none. Every decision of the three profiles was made, and T-GPU-14 passed.

The first of these runs found a container `lyrveil-gpu-test-lyrveil-llm-1` and 3113 MiB in use, left by an earlier run
that was stopped before its cleanup. The level's first step removed it, and the trials started at 840 MiB.
The first earlier run, `tuning-20261125-200134.json`, stored `gemma-4-12b/batch-256` with the problem "2 of 2 long
prompts failed" (curl status 2). That record is never reused, and the trial was measured again without problems in
`tuning-20261125-201018.json`. Its cause was not recorded.

### Steps 1 to 5: baseline, level A, margin rule, context step

The baseline is the lowest of three NVML samples taken 2 s apart before the first trial. In each trial "Peak" is the
highest VRAM sample, the margin is 12282 MiB minus the peak, and the first token is the slowest of two new long prompts.

| Profile | Context | Ready | Peak | Margin | RAM | Median over 20 replies | First token | Step 4 | Step 5: next context, margin |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gemma-4-12b` | 16384 | 37 s | 9000 MiB | 3282 MiB | 1762 MiB | 48.9 T/s | 7.67 s | A | 24576, 3146 MiB: grows |
| `gemma-4-26b-a4b` | 16384 | 61.5 s | 11091 MiB | 1191 MiB | 7951 MiB | 42.6 T/s | 9.04 s (6120 tokens) | A | 24576, 1431 MiB: grows |
| `rocinante-x-12b` | 8192 | 33 s | 9412 MiB | 2870 MiB | 1520 MiB | 53.0 T/s | 2.23 s | A | 12288, 2226 MiB: grows |

The first long prompt after a load is the slow one for the Gemma 4 profiles: 7.67 s against 2.09 s for the second, for
`gemma-4-12b`. For `rocinante-x-12b` both took about 2.1 to 2.2 s. GPU.2 measured `gemma-4-26b-a4b` at 885 and
1037 MiB of margin. This run measured 1191 MiB, so its margin still moves with what other programs hold, around the
1024 MiB line.

### Step 6: batch size

The prompt speed is the median of the long prompts after the first, in tokens per second, because the first prompt
after a load is a warm-up. The margin is in MiB.

| Profile | 256 | 512 | 1024 | 2048 | Kept |
| --- | --- | --- | --- | --- | --- |
| `gemma-4-12b` | 2827 (3646) | 2931 (3282) | 2961, +1.0 % (2552) | 2625 (1314) | 512 |
| `gemma-4-26b-a4b` | 1160 (1219) | 1830 (1191) | 2579, +41.0 % (1339) | 3068, +67.6 % (1225) | 2048 |
| `rocinante-x-12b` | 2653 (3024) | 2730 (2870) | 2869, +5.1 % (2562) | 2802 (1938) | 512 |

With its experts in RAM, `gemma-4-26b-a4b` gains from a larger batch, as the procedure expected. 2048 gains 67.6 % and
keeps 1225 MiB, so it is kept.

### Step 7 and T-GPU-14: the speed rule for the default profile

`gemma-4-26b-a4b` is the target default. Each setting ran 20 replies of 200 tokens and two new long prompts.

| Setting | VRAM loaded | Margin | RAM | Median | First token | Speed rule |
| --- | --- | --- | --- | --- | --- | --- |
| AutoFit (`MOE_CPU_LAYERS=0`) | 11083 MiB | 1191 MiB | 7951 MiB | 42.6 T/s | 9.04 s, 6120 tokens | holds |
| `MOE_CPU_LAYERS=8` | 3115 MiB | 9159 MiB | 15637 MiB | 21.2 T/s | 13.48 s, 6121 tokens | holds |
| `MOE_CPU_LAYERS=12` | 3115 MiB | 9159 MiB | 15862 MiB | 21.3 T/s | 13.34 s, 6120 tokens | holds |
| `MOE_CPU_LAYERS=16` | 3135 MiB | 9145 MiB | 15288 MiB | 21.1 T/s | 13.31 s, 6120 tokens | holds |

**T-GPU-14 passes.** AutoFit is the fastest setting that keeps the margin rule and meets the speed rule: a median of
42.6 tokens per second, at least 15, and the first token of a 6120-token prompt in 9.04 s, within 20 s. So
`MOE_CPU_LAYERS` stays 0, `gemma-4-26b-a4b` stays the target default, and A-12 holds.

All three `MOE_CPU_LAYERS` values loaded about the same 3.1 GiB of VRAM and kept 15.3 to 15.9 GiB in RAM. With
`GPU_LAYERS=-1`, KoboldCpp 1.124.2 did not fill the VRAM that `--moecpu` freed. The cause was not determined, because
the runtime's log was not kept. The procedure compares AutoFit with these three values at the catalog's
`GPU_LAYERS=-1`. A fixed `GPU_LAYERS` with `MOE_CPU_LAYERS` was not measured.

### Step 8: memory mapping

Each trial loaded once, ran an image job on `lyrveil-image` (`Illustrious-XL-v2.0.safetensors`, 768x768, 8 steps), and
then measured the reload and 5 replies.

| Profile | RAM, 0 / 1 | Reload, 0 / 1 | Speed cost of 1 | Kept |
| --- | --- | --- | --- | --- |
| `gemma-4-12b` | 1571 / 781 MiB (saves 790 MiB) | 37 / 37 s (0 %) | -0.2 % | 0 |
| `gemma-4-26b-a4b` | 6604 / 1867 MiB (saves 4737 MiB) | 55.6 / 49.6 s (10.8 %) | 1.6 % | 1 |
| `rocinante-x-12b` | 1439 / 1493 MiB (saves -54 MiB) | equal (0 %) | 0.1 % | 0 |

`USE_MMAP=1` saves at least 2 GiB of RAM only for `gemma-4-26b-a4b`. It costs 1.6 % of speed there, within 5 %, so
1 is kept for that profile.

### Step 9: KV cache type

| Profile | Peak freed by `q8_0` | Speed cost | Margin with `q8_0` at the next context | Measured part | Kept |
| --- | --- | --- | --- | --- | --- |
| `gemma-4-12b` | 340 MiB | 4.5 % | 3546 MiB | qualifies | `f16` until P04 |
| `gemma-4-26b-a4b` | 10 MiB | -0.2 % | 1385 MiB | does not qualify | `f16` |
| `rocinante-x-12b` | 611 MiB | 5.9 % | 3137 MiB | does not qualify (cost over 5 %) | `f16` |

The context of every profile already grows with `f16`, so `q8_0` cannot qualify by allowing the next step. For
`gemma-4-12b` it frees at least 256 MiB at a cost under 5 %, but the P04 Italian protocol has not scored it, so `f16`
stays ([ADR 0030](../decisions/0030-koboldcpp-efficiency-levers.md)). Under AutoFit, a smaller KV cache freed almost
nothing for `gemma-4-26b-a4b`. AutoFit probably put more layers on the GPU in its place, but that is inferred, not
logged.

### The decisions

`Get-GpuTuningDecision` computed every value in this table from the stored trials. None was set by hand.

| Profile | Default level | Context | Batch | `MOE_CPU_LAYERS` | `USE_MMAP` | `KV_QUANT` |
| --- | --- | --- | --- | --- | --- | --- |
| `gemma-4-12b` | A | 24576 | 512 | 0 | 0 | `f16` (`q8_0` qualifies on the measurements, pending P04) |
| `gemma-4-26b-a4b` | A | 24576 | 2048 | 0 (AutoFit) | 1 | `f16` |
| `rocinante-x-12b` | A | 12288 | 512 | 0 | 0 | `f16` |

## Not run or not verified

- `config/model-catalog.json` is unchanged. Applying these values, and the catalog's VRAM values, is P02.7.
- Each trial changes one lever from level A. Combinations were not measured. For example, `gemma-4-26b-a4b` with
  context 24576, batch 2048 and `USE_MMAP=1` together may have a different margin from any single trial, so each
  combination must be loaded once before it ships.
- Under AutoFit the margin is mostly the reserve AutoFit leaves, not the cost of a lever.
- No single run measured every trial. The full level, `scripts/lyrveil.cmd test gpu` without selectors, takes longer
  than the 10-minute limit of a tool call here. T-GPU-01 to T-GPU-04 were not run again for this task.
- The reload time is the wait of `docker compose up --wait` until the health check passes. The check's start interval
  is 2 s, so the resolution is about 2 s. Both 12B profiles read the same time with and without mmap.
- `docker stats` includes the page cache, so the RAM figures are upper bounds. The peak is sampled every 250 ms, so a
  shorter spike can be missed.
- The P04 measurements (SWA padding, SmartCache) and the Italian protocol of step 9 are not part of this task.
