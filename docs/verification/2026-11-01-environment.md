# Environment and upstream verification, 2026-11-01

Purpose: record what was really measured before implementation. Later phases add
their evidence as new files in this folder.

## Reference machine

| Item | Result | Command |
| --- | --- | --- |
| Operating system | Windows 11 Pro 10.0.26300 | System information |
| GPU | NVIDIA GeForce RTX 4070, 12282 MiB | `nvidia-smi --query-gpu=name,memory.total --format=csv` |
| Driver, CUDA user-mode version | 617.42, 13.4 | `nvidia-smi` |
| VRAM at idle | 1489 MiB used, 10522 MiB free | `nvidia-smi --query-gpu=memory.used,memory.free --format=csv` |
| Installed RAM | One 16 GB module, 15.8 GiB visible | `Win32_PhysicalMemory`, `Win32_ComputerSystem` |
| Memory visible to Docker | 8,203,943,936 bytes (7.64 GiB) | `docker info` |
| `.wslconfig` | Absent, so the WSL2 default applies | File check |
| Docker | Engine 29.9.2, API 1.56, minimum API 1.40, Docker Desktop | `docker version` |
| Docker Compose | v5.6.1 | `docker compose version` |
| NVIDIA runtime | `nvidia` listed. Default runtime `runc` | `docker info` |
| WSL2 distributions | `docker-desktop` only, no Ubuntu | `wsl -l -v` |
| Host tools | Git 2.56.0, Node.js 24.22.1, Python 3.15.1 | `--version` |
| Free disk | 636 GB on `C:` | `df -h` |
| GitHub CLI | Not installed | `gh --version` |

## Differences from the target

| Target | Measured | Decision |
| --- | --- | --- |
| 32 GB RAM | 16 GB | Supported as a reduced tier. See [ADR 0019](../decisions/0019-hardware-tiers.md). |
| Enough memory for Docker | 7.64 GiB, below the 11 GiB minimum | `setup.sh` stops with the exact `.wslconfig` to apply. See [Operations](../operations.md#wsl2-memory). |
| Linux filesystem of WSL2 | Ubuntu distribution absent. This checkout is on `C:` | The documentation can live anywhere. Development from P01 on needs a clone inside Ubuntu. See [Operations](../operations.md#first-installation). |

## GPU access inside a container

An existing unrelated local image ran with no network and `--gpus all`.

| Check | Result |
| --- | --- |
| `nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu` | RTX 4070, 12282 MiB, 1489 MiB, 10522 MiB, 7 % |
| `nvidia-smi --query-compute-apps=pid,used_memory` | Empty output |
| `/dev/dxg` | Present |
| `/dev/nvidia*` | Absent |
| `libnvidia-ml.so.1` | Present |

GPU passthrough works on this machine. No model has been loaded.

## Docker socket inside a container

`ls -ln /var/run/docker.sock` shows `srw-rw---- 1 0 0`: owner and group 0, mode 660. A
non-root process needs supplementary group 0 to use it, and the design relies on that.

## SQLite

Host Python with SQLite 3.53.4:

- FTS5 with `tokenize="unicode61 remove_diacritics 2"` matched `citta` against `città`.
- `bm25()` returned `-1e-06` on a one-row table. Small corpora give near-zero scores,
  so [retrieval](../memory-engine.md#scoring) normalizes within the candidate set.

The application image was not available to test. P01 verifies FTS5 and WAL in the real
image, and the backend repeats that check at every start.

## Upstream facts

Read from official registries, repositories and Hugging Face APIs on 2026-11-01:

- Runtime tags, asset sizes and digests: [Technology choices](../technology.md#ai-runtimes-and-models).
- Model revisions, sizes, hashes and gating: same section.
- Dependency versions and licenses: [Technology choices](../technology.md).
- Flags, endpoints and tokenizer facts: [Runtime contracts](../runtime-contracts.md).
- Licenses: [Licensing](../licensing.md).

## Not executed

- No application code exists, so no test suite has run.
- No model has been downloaded or loaded. All VRAM figures for models are estimates.
- Hashes were read from upstream metadata, not computed from downloaded files.
- gitleaks was not run: see [Publishing](../publishing.md#documentation-phase-checks).
