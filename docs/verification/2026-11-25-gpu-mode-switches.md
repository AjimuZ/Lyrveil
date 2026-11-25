# GPU.6: mode switches, the VRAM hold and a text during a render on the reference machine

Date: 2026-11-25, 23:28 to 23:58 local time, and a final run on 2026-11-26 at 00:10 (the orchestrator's events are in
UTC, one hour earlier). Task GPU.6:
T-GPU-07 to T-GPU-09 in the `gpu` level, `tests/gpu/ModeSwitch.Tests.ps1`
([The `gpu` level](../testing.md#the-gpu-level), [ADR 0046](../decisions/0046-test-only-orchestrator-for-the-gpu-level.md)).

## Machine and versions

- NVIDIA GeForce RTX 4070, 12282 MiB; 843 to 865 MiB in use by the desktop before each run (the baseline).
- Docker Desktop with the WSL2 backend; ComfyUI 0.43.0 with PyTorch 2.14.1+cu130 (from its log); KoboldCpp v1.124.2;
  Pester 5.7.1; Windows PowerShell 5.1.
- Profiles: `rocinante-x-12b` at the catalog's level A (context 12288, batch 512, `GPU_LAYERS=-1`, `USE_MMAP=0`,
  `f16`), and the checkpoint of `illustrious-xl-v2.0`, the defaults of the target tier.
- Stack of the project `lyrveil-gpu-test`: the two runtimes as in `compose.yaml`, the test-only orchestrator
  `lyrveil/orchestrator:gpu-test` (stage `gpu-test-runtime`) with the host's Docker socket, and, for T-GPU-08, the
  backend's runtime image `lyrveil/backend:gpu-test` on the test data volume. `lyrveil-models` was mounted read-only.
  No installation container, `lyrveil-data` or backup was touched. Each run ended with "no container or volume of the
  test project is left".

## Runs

Every command is `scripts/lyrveil.cmd test gpu ModeSwitch` with the items shown. The results files are in
`test-results/gpu/`, which Git ignores, and were not copied here; the numbers below come from them.

| Items | Pester | Result | Results file |
| --- | --- | --- | --- |
| `t-gpu-09` | 7 passed, 9 skipped, 71 s | Passed | `mode-switches-20261125-232809.json` |
| `t-gpu-07 rounds-2` | 4 passed, 3 failed, 406 s | Failed: round 2 never reached `IMAGE_READY` | `mode-switches-20261125-232938.json` |
| `t-gpu-07 rounds-2`, with the runtime's log kept on failure | 4 passed, 3 failed, 411 s | Failed the same way; the log gave the cause | `mode-switches-20261125-233801.json` |
| `t-gpu-07 rounds-2`, after the image fix | 6 passed, 1 failed, 152 s | Both rounds completed; the test's own timing failed one check | `mode-switches-20261125-234650.json` |
| `t-gpu-08` | 6 passed, 10 skipped, 132 s | Passed | `mode-switches-20261125-234959.json` |
| `t-gpu-07 rounds-5` | 7 passed, 9 skipped, 318 s | Passed | `mode-switches-20261125-235230.json` |
| `t-gpu-07 rounds-5` | 7 passed, 9 skipped, 316 s | Passed | `mode-switches-20261125-235800.json` |
| `rounds-2`: all three parts in file order, on 2026-11-26 at 00:10, with the final code | 16 passed, 0 failed, 284 s | Passed | `mode-switches-20261126-001004.json` |

T-GPU-07 asks for ten round trips. They ran as two runs of five, each on a fresh stack, because one shell call of
this session is limited to 10 minutes and ten round trips with the setup take about 11. The default selection, all
three parts with ten round trips in one run, has not run.

## The fault found: a second start of the image runtime

The first two-round run failed in round 2: the orchestrator started `lyrveil-image` three times, each start exited
with status 1 (`IMAGE_LOAD_FAILED`, `oom` 0), and after the third the orchestrator gave the GPU back to text, as
[Load failures](../gpu-orchestration.md#load-failures-and-launch-levels) says. The same run again, keeping the
runtime's log, showed the entrypoint at every later start:

```text
mkdir: cannot create directory '/tmp/comfy/output': Permission denied
```

The first start of the container worked, every later start of the same container did not. The orchestrator starts
the same container for every image job, so in an installation every image job after the container's first run would
have failed. The text runtime restarted fine: its tmpfs is `/tmp`, which exists in its image with mode 1777.

Cause, as the runs show it: the image had no `/tmp/comfy`. The container runtime created the mount point with mode
0755 at the first start, and the folder stayed in the container. At every later start the new tmpfs took the mode of
that existing folder, owned by root, so user 10001 could not write it. Fix: `docker/image/Dockerfile` creates
`/tmp/comfy` with mode 1777 in the runtime stage. After it, every later start in every run worked: one restart in the
third two-round run and four in each five-round run. `tests/docker/ImageRuntime.Tests.ps1` now starts one container
twice in CPU mode and expects mode 1777 at the second start; it was written and has not run.

The third two-round run completed both rounds (full switches of 43.71 and 39.67 s). It failed only "never shows both
models in the NVML samples": the first round began right after the sampler started, so the first phase of the text
model held one NVML sample and no container sample. The test now waits 4 s before the first switch and after the
last one.

## T-GPU-09: another program holds the VRAM

From `mode-switches-20261125-232809.json`. The test's own container `lyrveil-gpu-test-vram-load` (the image runtime's
image, PyTorch, no network, no capability) held 8192 MiB, leaving NVML at 9210 of 12282 MiB in use. Then the
orchestrator started:

| Moment | State | `blocked_reason` | `blocked_vram` |
| --- | --- | --- | --- |
| After the start, desired mode `llm` | `IDLE` | `GPU_INSUFFICIENT_VRAM` | `llm`, 2800 MiB free, 6000 required |
| After `PUT /v1/desired-mode image` | `IDLE` | `GPU_INSUFFICIENT_VRAM` | `image`, 2800 free, 6500 required |
| 12 s later | `IDLE` | `GPU_INSUFFICIENT_VRAM` | `image`, 2800 free, 6500 required |
| After `PUT /v1/desired-mode llm` | `IDLE` | `GPU_INSUFFICIENT_VRAM` | `llm`, 2800 free, 6000 required |

- NVML read 3072 MiB free at the first hold; the orchestrator's 2800 MiB is within the 512 MiB the test allows.
- Neither runtime container had ever started during the hold (`StartedAt` was still the zero time).
- The load container kept PID 11005, its start time `2026-11-25T22:28:14.15602417Z` and no restart until the test
  removed it by the id its own start returned.
- The events hold three hold notes, from `IDLE` to `IDLE` with `GPU_INSUFFICIENT_VRAM` and both numbers, one for each
  mode the hold waited for.
- After the removal, `LLM_READY` came 38.73 s later, with `blocked_reason` null: at most 5 s to the next check, then
  the text load.

## T-GPU-07: ten round trips

From `mode-switches-20261125-235230.json` (run 1) and `mode-switches-20261125-235800.json` (run 2). Each round trip:
mode `image`, a 768 x 768, 8-step render under an image lease on a fresh ComfyUI, mode `llm`, 16 tokens under a text
lease. Times from the orchestrator's events, in seconds:

| Run, round | To image: stop and release, load | To text: stop and release, load | Full switch | Round trip seen by the test |
| --- | --- | --- | --- | --- |
| 1, 1 | 1.37, 8.26 | 1.78, 32.33 | 43.74 | 55.81 |
| 1, 2 | 1.36, 6.26 | 1.83, 32.37 | 41.82 | 53.75 |
| 1, 3 | 1.43, 6.26 | 1.78, 32.35 | 41.82 | 53.59 |
| 1, 4 | 1.31, 6.26 | 1.82, 32.36 | 41.74 | 53.58 |
| 1, 5 | 1.38, 6.28 | 1.81, 32.32 | 41.78 | 53.57 |
| 2, 1 | 1.48, 8.30 | 1.81, 30.34 | 41.92 | 53.87 |
| 2, 2 | 1.32, 6.27 | 1.82, 32.35 | 41.77 | 53.53 |
| 2, 3 | 1.35, 6.26 | 1.81, 32.33 | 41.75 | 53.60 |
| 2, 4 | 1.43, 6.27 | 1.78, 32.33 | 41.81 | 53.76 |
| 2, 5 | 1.45, 6.27 | 1.81, 32.35 | 41.88 | 53.75 |

- Full switch: 41.74 to 43.74 s, mean 42.18 s in run 1 and 41.83 s in run 2, against the planning target of 150 s
  (A-06). The text model's load is 30.34 to 32.37 s of it.
- Every render ended `success` with one image, in 9.03 to 9.15 s with the checkpoint load; every reply had text, such
  as "Il porto al tramonto è uno spettacolo magico.".
- Every release was verified with the full drop: 9209 MiB for the text runtime, its load footprint, and 6871 MiB for
  the image runtime, whose load footprint is its CUDA context alone (153 MiB), since the checkpoint loads at the
  render.
- Samples, run 1 and run 2: 881 and 874 NVML samples, 769 and 762 container samples. No container sample showed both
  runtimes running.
- NVML showed eleven model phases in each run, text then image and text five times, each with container samples of
  its own runtime alone. Every release between them went down to 842 to 853 MiB, the desktop's own use. The highest
  sample was 10072 MiB in run 1 and 10053 MiB in run 2, both in a text phase; the image phases peaked at 7773 to
  9251 MiB.
- The two runtime containers kept their ids from the first switch to the last: the orchestrator only stopped and
  started them.

## T-GPU-08: a text during a real render

From `mode-switches-20261125-234959.json`, with the backend on the test data volume, a demo character, and a
**Rapida** square image request. The orchestrator's events (UTC) and the backend's record:

| Time | What |
| --- | --- |
| 22:50:39.331 to 22:50:42.664 | The image job's prompt stage under a text lease |
| 22:50:42.680 to 22:50:50.254 | Switch to image on behalf of the image job |
| 22:50:50.812 | Image lease granted (`GENERATING_IMAGE`) |
| 22:50:51.190 | The text reply saved, `queued` (the send answered 202) |
| 22:51:06.557 | Image lease released |
| 22:51:06.570 to 22:51:40.666 | Switch to text, on behalf of the reply's job |
| 22:51:41.476 to 22:51:44.126 | The reply's text lease, its first |

- The image message completed with an image, and its job `completed`.
- The reply completed with Italian text, and its job `completed`; it ended 54.2 s after the send as the test saw it,
  and 37.57 s after the image lease was released.

## The final run

`mode-switches-20261126-001004.json`, all three parts in one stack, after every change of this task:

- T-GPU-09: the load held 8192 MiB, both loads were held with 2821 MiB free, and `LLM_READY` came 38.85 s after the
  load container was removed.
- T-GPU-07, two round trips: full switches of 43.87 s (stop and release 1.34 s, ComfyUI 10.28 s; 1.91 s, KoboldCpp
  30.33 s) and 41.68 s, with no container sample of both runtimes and five NVML model phases.
- T-GPU-08: the image job ran on an image container that had already started twice, and the reply completed 52 s after
  the send, 33.93 s after the image lease was released.

## Other checks

- `scripts/lyrveil.cmd test unit tests/unit/test_gpu_test_gate.py`: 8 passed in the orchestrator's test stage.
- `scripts/lyrveil.cmd test lint`: the first run failed on one PSScriptAnalyzer finding in `Start-GpuTimelineSampler`
  (a variable of a background job without `$using:`); the job now reads `$using:ContainerId`. That change came after
  the two five-round runs and before the final run. The second lint run passed, with 0 analyzer findings.

## Not run or not verified

- The ten round trips of T-GPU-07 in one run, and the default selection of the file in one run (see Runs).
- The new Pester cases: `tests/scripts/GpuSwitches.Tests.ps1` and the configuration cases of
  `tests/scripts/TestCommand.Tests.ps1` (unit level), and the restart case of `tests/docker/ImageRuntime.Tests.ps1`
  (docker level).
- T-GPU-07 renders a small graph, not the backend's **Rapida** or **Alta** graphs; T-GPU-08 rendered the backend's
  **Rapida** graph once. No image was looked at.
- NVML cannot list processes on WSL2, so "both models" is judged from the memory phases and the container states,
  not per process. "No process touched" is judged from the load container's PID, start time and restarts; the gate's
  refusal of every other container is covered by `orchestrator/tests/unit/test_gpu_test_gate.py`.
- The backend's own waits (the 120 s VRAM wait of T-ORC-05, the image wait limit, batches of images) did not run on
  the GPU, nor did T-GPU-10 to T-GPU-12.
