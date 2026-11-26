# GPU.7: stop.cmd and the offline stack on the reference machine, and why T-GPU-10 is pending

Date: 2026-11-26, 20:01 to 20:17 local time (the orchestrator's events and Docker's times are in UTC, one hour
earlier). Task GPU.7: T-GPU-11 and T-GPU-12 in the `gpu` level, `tests/gpu/Resilience.Tests.ps1`
([The `gpu` level](../testing.md#the-gpu-level)). T-GPU-10 is written in the same file and is pending GPU.7b.

## Machine and versions

- NVIDIA GeForce RTX 4070, 12282 MiB; 600 to 633 MiB in use by the desktop before each run (the baseline).
- Docker Desktop with the WSL2 backend; KoboldCpp v1.124.2; Pester 5.7.1; Windows PowerShell 5.1.
- Profiles: `rocinante-x-12b` at the catalog's level A (context 12288, batch 512, `GPU_LAYERS=-1`), and the
  default image checkpoint of the target tier for the backend's **Rapida** square image.
- Stack of the project `lyrveil-gpu-test`, as in [GPU.6](2026-11-25-gpu-mode-switches.md): the two runtimes as in
  `compose.yaml`, the test-only orchestrator with the host's Docker socket, and the backend's runtime image on the test
  data volume. `lyrveil-models` was mounted read-only. No installation container, `lyrveil-data`, `.env` or backup
  folder was touched. Each run ended with "no container or volume of the test project is left".

## Runs

The command is `scripts/lyrveil.cmd test gpu Resilience` with the items shown. The results files are in
`test-results/gpu/`, which Git ignores, and were not copied here; the numbers below come from them.

| Items | Pester | Result | Results file |
| --- | --- | --- | --- |
| none | 13 passed, 1 failed, 5 skipped, 177 s | Failed: a test defect, see below | `resilience-20261126-200141.json` |
| none | 14 passed, 0 failed, 5 skipped | Passed; the 5 skipped are the T-GPU-10 cases, pending GPU.7b | `resilience-20261126-200511.json` |
| `t-gpu-10` | 5 passed, 2 failed, 12 skipped | Failed, as expected on this machine: no allocation failure happened | `resilience-20261126-200818.json` |
| none, with the final code | 14 passed, 0 failed, 5 skipped, 169.6 s | Passed; the 5 skipped are the T-GPU-10 cases | `resilience-20261126-201456.json` |

The first run failed only "keeps every service on internal networks": every service seemed to join no network.
`Get-GpuTestNetworkFact` read a bare `docker inspect`, whose JSON array Windows PowerShell 5.1 parses as one array
object, so it looked for the networks on the array. It now reads `docker inspect --format '{{json .}}'`, one object.
The judge failed closed, as it should for a record with no network.

An earlier attempt of this task, interrupted before it was committed, ran a previous version of the file from 19:10 to
19:36 the same day. Its code was lost and this version was written again; its results files
(`resilience-20261126-191043.json` to `resilience-20261126-193624.json`) remain, and only its T-GPU-10 numbers are
cited here.

## T-GPU-12: the stack with no way out of its internal networks

From `resilience-20261126-200511.json`. The project's backend, orchestrator and runtimes join only the internal
networks of `compose.yaml` (ADR 0003, T-DCK-08), and the frontend, the only service on the edge network, is not part
of the project. The test checked this on the running containers, then used the stack:

| Service | Networks, all `Internal: true` | Published ports | Default route | 1.1.1.1:443 | example.com |
| --- | --- | --- | --- | --- | --- |
| `lyrveil-backend` | `lyrveil-gpu-test_lyrveil-app`, `_lyrveil-control`, `_lyrveil-runtime` | none | none | unreachable | unresolved |
| `lyrveil-orchestrator` | `lyrveil-gpu-test_lyrveil-control` | none | none | unreachable | unresolved |
| `lyrveil-llm` | `lyrveil-gpu-test_lyrveil-runtime` | none | none | unreachable | unresolved |
| `lyrveil-image` | `lyrveil-gpu-test_lyrveil-runtime` | none | none | unreachable | unresolved |

- The probe ran inside each container: Python in the backend, the orchestrator and the image runtime, the shell with
  `curl` and `getent` in the text runtime, which has no Python. The image runtime was probed while it ran for the
  image job.
- The control, a one-off container on Docker's default bridge, had a default route, so the route check can fail. It
  opened no connection.
- A text reply completed: its job ran 0.96 s (19:05:52.970 to 19:05:53.931), 3.3 s from the send as the test saw it.
- A **Rapida** square image completed with an image: its job ran 30.5 s (19:05:56.215 to 19:06:26.676), with the
  switch to the image runtime (release of the text runtime verified, drop 9218 MiB) and back (drop 6871 MiB).
- A second reply, after the image, completed: its job ran 32.7 s, the switch back to the text runtime included.

## T-GPU-11: stop.cmd, no orphan process and the memory given back

From `resilience-20261126-200511.json`, on the stack T-GPU-12 left running: the text runtime ready, the backend and
the orchestrator running, the image runtime stopped. The test runs `Invoke-LyrveilStop`, the code of `stop.cmd`, with
the project `lyrveil-gpu-test`, the two compose files, an env file that does not exist (so Compose reads
`.env.example`, never the installation's `.env`) and a backup folder in Pester's test drive.

- Before the stop, NVML read 9809 MiB in use. `docker top` listed six processes: `docker-init` and the main process of
  the backend (`python -m lyrveil.main`), the orchestrator (`python gpu_probe/gpu_test_orchestrator.py`) and the text
  runtime (`koboldcpp-launcher --model ... --contextsize 12288 --gpulayers -1 ...`). A one-off container in the Docker
  VM's PID namespace (`--pid host`, no network, no capability, user 10001) saw all six by PID and command line, and the
  KoboldCpp process by its program name.
- The stop exited 0 in 28.5 s. Its warnings: only "Backups: not copied", because the test data volume has no
  installation label; the test's backup folder was never created. Neither drain step warned. The console of this run
  was filtered and did not show the backend's own lines; in the first run they were "PASS Queue drain: the job queue
  is paused and no job is running (waited 0 s)" and "PASS Runtime release: the runtime is stopped and the release of
  the GPU memory is verified".
- The orchestrator's log holds the drain: `LLM_READY` to `UNLOADING_LLM` with reason `drain` at 19:07:07.807, then
  `IDLE` with `release verified` at 19:07:09.143, exit code 143 and a drop of 9217 MiB (footprint 9208 MiB).
- Right after the stop the same probe saw none of the six processes and no KoboldCpp or ComfyUI process.
- All four containers were `exited` with exit code 143 (SIGTERM), none killed for memory: the text runtime at
  19:07:07.95 (by the drain), the backend and the orchestrator at 19:07:34.18 and 19:07:34.26, the image runtime at
  19:06:26.93 (by the switch back). Ten seconds later none had started again.
- NVML read 596 MiB in use, against the run's baseline of 600 MiB: the limit is the baseline plus 128 MiB.

## T-GPU-10: pending GPU.7b

T-GPU-10 needs a real CUDA out-of-memory at load, then `LLM_LOAD_FAILED` with `oom`, the backend lowering the launch
file to level B, and a level-B load. The file forces it: the test's own VRAM load (the container of T-GPU-09) holds
memory until at most 6912 MiB are free, above the 6000 MiB the orchestrator requires, and the launch file is level A
with `GPU_LAYERS=99`, `CONTEXT_SIZE=24576` and `BATCH_SIZE=2048`. From `resilience-20261126-200818.json`:

- The VRAM load held 4352 MiB; NVML read 5104 MiB in use. The orchestrator held nothing back and started the load.
- KoboldCpp's log: `using device CUDA0 ... - 11090 MiB free`, then CUDA0 buffers of 6763.3 MiB (model), 3880 MiB (KV)
  and 1281.06 MiB (compute), 11924.4 MiB in all, with no allocation failure line.
- `LLM_READY` came 28.0 s after the request. The orchestrator measured a footprint of 6724 MiB (5374 to 12098 MiB in
  use), and `/api/extra/true_max_context_length` was 24576. The launch file stayed at level A.
- A chat reply completed; its job ran 3.3 s. In the interrupted attempt's run (`resilience-20261126-193624.json`) the
  same forced load gave the same 11090 MiB free and 11924.4 MiB of buffers, and a reply job of 26.7 s.

CUDA reported memory as free that the other container held, and the load placed about 5200 MiB more on CUDA0 than NVML
saw it take. The likely cause, an inference not measured directly, is the "CUDA - Sysmem Fallback Policy" of the
NVIDIA driver on Windows, which moves what does not fit into shared system memory instead of failing. Until the owner
sets that policy or redefines T-GPU-10 for Windows (GPU.7b), the five T-GPU-10 cases are reported as skipped with this
reason in every run that does not select `t-gpu-10`, and are never counted as passed.

## The final run

After the passing run three things changed: the text of the pending reason, one console line, and the level-B case
of T-GPU-10, which now also requires `LEVEL=B` and the failed load before it. None of them is a check of T-GPU-11 or
T-GPU-12. The default selection then ran again with the final code at 20:14 (`resilience-20261126-201456.json`):
14 passed, 0 failed, 5 skipped, and every T-GPU-10 case was skipped with the reason above.

- T-GPU-12: the same networks, ports and probe results for all four services, and a control with a default route.
  The first reply's job ran 1.1 s, the image job 36.7 s, the second reply's job 32.8 s with the switch back. The
  switch released 9219 MiB for the text runtime and 6858 MiB for the image runtime.
- T-GPU-11: NVML read 9724 MiB before the stop and 510 MiB after it, against a baseline of 513 MiB. The stop took
  28.5 s, warned only about backups, and drained the text runtime with a verified release of 9219 MiB. All four
  containers exited with status 143. The probe saw the KoboldCpp process before the stop and no process of the stack
  after it.

## Other checks

- `tests/scripts/GpuResilience.Tests.ps1`, in the `unit` level, covers the readers and judges of
  `scripts/lib/GpuResilience.psm1`; it was written and has not run.
- `scripts/lyrveil.cmd test lint` passed with the final code and documents: PSScriptAnalyzer 1.24.0 found 0 problems
  in 42 scripts, and the documentation checks found no broken link or anchor among 1332.

## Not run or not verified

- A manual unplug. T-GPU-12 shows that the backend, the orchestrator and both runtimes work with no route out, on the
  real GPU. It does not cover the frontend and the browser (the frontend joins the edge network to publish
  `127.0.0.1:4200` and is not part of the test project), nor Windows, WSL2 and Docker Desktop starting with no network
  adapter, nor `start.cmd` itself. For AC-18 as a whole, the reference machine still needs one manual check with its
  network disconnected: run `start.cmd`, open the app, send a message and ask for an image.
- `stop.cmd` itself on an installation, with its real backup copy: T-GPU-11 runs the same code on the test project,
  where the backup copy is refused by design.
- A stop during a running reply or render; T-GPU-11 stops a stack at rest.
- NVML cannot list processes on WSL2, so "no orphan process" is judged from the Docker VM's process table and the
  containers' states, and "full release" from NVML's total in use. Windows processes are not checked.
- T-GPU-10's allocation failure, the lowering and the level-B load (pending GPU.7b).
