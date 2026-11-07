# Image runtime image, 2026-11-07

Task P05.1, from Git Bash on Windows 11 with Docker Desktop, on 2026-11-07. Only what was run is listed as run.

## Written

- `docker/image/Dockerfile`, with the stages `source`, `uv`, `lock`, `lockfile`, `deps` and `runtime`, and
  `docker/image/entrypoint.sh` ([Operations](../operations.md#dockerimagedockerfile)).
- `docker/image/requirements.lock`, generated in a container, not by hand.
- The service `lyrveil-image` of the `ai` profile in `compose.yaml`, as in the reference Compose file.
- `tests/docker/ImageRuntime.Tests.ps1`. The setup override puts the fake ComfyUI in place of the image, and the
  lifecycle, data and models overrides leave it out with a profile that no command enables. `Setup.Tests.ps1` now
  expects the two runtime containers.

## Run

| Command | Result |
| --- | --- |
| `docker build -f docker/image/Dockerfile --target lockfile --output type=local,dest=docker/image .` | Wrote the lock: 103 packages, each with one version and its SHA-256 hashes. `torch==2.14.1+cu130`, `torchvision==0.29.1+cu130` and `triton==3.8.0`; the NVIDIA wheels are CUDA 13 packages, such as `nvidia-cuda-runtime==13.0.96` and `nvidia-cudnn-cu13` |
| `docker build --quiet -f docker/image/Dockerfile -t lyrveil/image:test .` | Built. The commit check, `uv pip sync --require-hashes --only-binary :all:`, `uv pip check`, the `--no-index` install of the commit's `requirements.txt` and the CUDA 13.0 check all passed |
| `docker image inspect lyrveil/image:test` | 11,578,306,432 bytes, user `10001:10001`, entrypoint `/usr/local/bin/lyrveil-image-entrypoint`, `HOME=/tmp/comfy/home`, `TMPDIR=/tmp/comfy/tmp` |
| A temporary build stage on top of `runtime`, removed afterwards | As root: `find` for `.safetensors`, `.ckpt`, `.pt`, `.pth`, `.gguf`, `.sft`, `.bin` and `.onnx` found only `site-packages/distutils-precedence.pth` (a Python path file) and the four `.bin` normalization tables of `sentencepiece`; `models/checkpoints` holds only `put_checkpoints_here`. As user 10001 with `RUN --network=none`: the entrypoint with `--cpu` and `RESERVE_VRAM_GB=1.5` answered `GET /system_stats` after 6 s with `comfyui_version` `0.43.0`, `pytorch_version` `2.14.1+cu130`, device `cpu` and the documented flags in `argv`. It wrote only under `/tmp/comfy`, including `tmp/torchinductor_uid_10001` through `TMPDIR`. The log showed no database or write error |
| `scripts/lyrveil.cmd test lint` | Passed: 26 PowerShell scripts with 0 findings, documentation checks, backend, orchestrator and frontend |

## Not run

- `docker compose --profile ai config --quiet` and `docker compose --profile ai build lyrveil-image`: the session
  refused both. The final Dockerfile, after the temporary stage was removed, was built again with `docker build`. `tests/docker/Security.Tests.ps1` resolves
  `compose.yaml` with `--profile ai` and checks the user and the internal networks of every service, including
  `lyrveil-image`, at the `docker` level.
- `tests/docker/ImageRuntime.Tests.ps1` and the changed `Setup.Tests.ps1`: written, not run in this task. Only the
  `docker` level, which the orchestrator runs at the end of the plan, can start containers. The temporary build stage
  checked the CPU start on a writable root; the read-only root and the tmpfs `/tmp/comfy` are first checked by the
  Pester file.
- A GPU generation, a checkpoint load, the health check of `compose.yaml` and a start through the orchestrator: P05.2,
  P05.7 and T-GPU-06.
