# Environment and upstream verification, 2026-11-05

Purpose: record what was measured and read for the revision after the reference machine's RAM upgrade. It supports
ADRs [0024](../decisions/0024-selectable-model-profiles.md) to [0030](../decisions/0030-koboldcpp-efficiency-levers.md).
The first section was measured before any change. Later sections record what changed on the owner's request.

## Reference machine

| Item | Result | Command |
| --- | --- | --- |
| Installed RAM | Two Crucial CP16G60C36U5B.M8D1 modules, DDR5-5600, 16 GB each. 31.77 GiB visible | `Win32_PhysicalMemory`, `Win32_ComputerSystem` |
| Memory visible to Docker | 16,647,065,600 bytes (15.50 GiB). In preflight's warning band of 11 to 19.9 GiB | `docker info` |
| `.wslconfig` | Absent, so the WSL2 default of half the memory applies | File check |
| WSL | 3.0.3.0, kernel 6.18.54.1-1. Distributions: `docker-desktop` only, no Ubuntu | `wsl --version`, `wsl -l -v` |
| GPU | NVIDIA GeForce RTX 4070, 12282 MiB | `nvidia-smi` |
| Driver, CUDA user-mode version | 621.14, 13.5 | `nvidia-smi` |
| VRAM at idle | 1411 MiB used, 10600 MiB free | `nvidia-smi --query-gpu=memory.used,memory.free --format=csv` |
| CPU | Intel Core i5-14600KF, 14 cores, 20 threads | `Win32_Processor` |
| Page file | 24576 MB | `Win32_PageFileUsage` |
| Free disk | 595 GB on `C:` | `Get-PSDrive` |
| Docker | Engine 29.9.2, API 1.56, minimum API 1.40 | `docker version` |
| Docker Compose | v5.6.1 | `docker compose version` |

## Differences from the target

| Target | Measured | Decision |
| --- | --- | --- |
| 32 GB RAM | 32 GB | Target tier reached. See [ADR 0027](../decisions/0027-hardware-tiers-after-ram-upgrade.md) |
| Docker sees at least 20 GiB | 15.50 GiB | 32 GB [`.wslconfig`](../operations.md#wsl2-memory) applied the same day. See [After the changes](#after-the-changes) |
| A Linux distribution for the scripts | Absent | No longer required: Lyrveil runs from Windows ([ADR 0029](../decisions/0029-windows-operation-with-docker-volumes.md)) |

## Upstream re-verification

| Item | Result | Source |
| --- | --- | --- |
| KoboldCpp `v1.124.1` | The release API returns Not Found and the asset URL returns 404. The tag still exists | GitHub API, `LostRuins/koboldcpp` |
| KoboldCpp `v1.124.2` | Commit `6d2fedf8624e5f05926af3191f9efdcb13c4133c`. Asset `koboldcpp-linux-x64`, 628,981,944 bytes, SHA-256 `c66ae0106673c7d9c41bafcc92c95bc5a6256709e9d853e5e2255bf536c1b069` | GitHub release API, asset digest |
| KoboldCpp flags and endpoints | Every flag and endpoint in [Runtime contracts](../runtime-contracts.md) is present. `--moecpu` and the AutoFit default were read in `koboldcpp.py`, and prompt tokenization in `kcpp_src/gpttype_adapter.cpp` | Source at `v1.124.2` |
| ComfyUI | `v0.43.0` still pinned. `v0.44.0` was released on 2026-11-04 and is not adopted | GitHub releases |
| ComfyUI nodes | `ImageScaleBy` with `lanczos`, `VAEEncode`, `VAEEncodeTiled`, `LatentUpscaleBy` are core nodes | `nodes.py` at `v0.43.0` |
| Text profiles | Revisions, sizes and SHA-256 of all five files, licenses, gating | Hugging Face API, model cards |
| Gemma 4 template | Rendered in a sandbox from the official `chat_template.jinja` with Jinja2. The 12B, 26B-A4B and Orion templates give the same text. Control token identifiers read from `tokenizer.json` | Hugging Face files |
| Mistral v3 Tekken template | Rendered in a sandbox from the template in the Rocinante-X GGUF | Hugging Face files |
| Gemma 4 architecture | Layers, heads, window and expert counts | `config.json` of both models |
| Image profiles | Revisions, sizes and SHA-256 of all three files, license fields, NoobAI-XL settings | Hugging Face API, model cards |
| Illustrious XL v3.x | Not published as open weights, so not in the catalog | Hugging Face, OnomaAI announcements |

## Not executed

- No model was downloaded or loaded. Every memory, speed and time figure in [Model catalog](../models.md#memory-estimates),
  [GPU orchestration](../gpu-orchestration.md) and [Image generation](../image-generation.md#quality-presets) is an
  estimate until P02 and P05 measure it.
- No image was rendered, so the **Alta** quality gain and its time are not measured. The preset rule in
  [ADR 0026](../decisions/0026-two-pass-image-rendering.md) decides after P05.
- The reduced tier can no longer be measured on the reference machine. Its figures stay estimates until the tests run
  on a 16 GB machine.
- The `.wslconfig` was written by hand at the owner's request, not by a Lyrveil script, which never writes it.

## After the changes

At the owner's request, on the same day:

| Change | Result | Command |
| --- | --- | --- |
| `%UserProfile%\.wslconfig` with `memory=24GB` and `swap=8GB`, then `wsl --shutdown` and a Docker Desktop restart. No container was running | Docker sees 25,199,255,552 bytes (23.47 GiB) and 20 CPUs. Swap 8192 MB | `docker info`, `free -m` |
| Ubuntu 24.04.5 LTS installed with `--no-launch`. The owner created the Linux user and enabled Docker Desktop's WSL integration | Docker 29.9.2 and Compose v5.6.1 work from Ubuntu. Later found not needed, because the owner chose to work from Windows | `wsl -l -v`, `docker version` |
| GPU from a container, with an existing local image, no network, removed after the run | RTX 4070, 12282 MiB, 1870 MiB used, 10141 MiB free | `nvidia-smi --query-gpu=...` |

## Storage from Windows

A container from an existing local image, with no network, mounted a folder on `C:` at `/win` and a new named volume at
`/vol`. A script wrote 1 GiB with `dd ... conv=fsync`, read it back, and created 300 small files. Both runs:

| Operation | Folder on `C:`, run 1 / run 2 | Named volume, run 1 / run 2 |
| --- | --- | --- |
| Write 1 GiB with `fsync` | 352 / 320 MB/s | 2.3 / 2.6 GB/s |
| Read 1 GiB | 429 / 373 MB/s | 14.8 / 14.7 GB/s, from the page cache |
| 300 small files | 344 / 317 ms | 4 / 3 ms |

The volume and the folder were removed afterwards. The volume's read figure is the page cache, which is the case that
matters for reloading a model after an image. These numbers support [ADR 0029](../decisions/0029-windows-operation-with-docker-volumes.md).

## KoboldCpp efficiency flags

Read in `koboldcpp.py` at commit `6d2fedf8624e5f05926af3191f9efdcb13c4133c` (14,712 lines):

| Flag | Fact |
| --- | --- |
| `--usemmap` | "If set, uses mmap to load model". Off by default |
| `--batchsize` | Choices -1, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096. Default 512 |
| `--quantkv` | Choices `f16`, `bf16`, `q8_0`, `q5_1`, `q4_0`. Default `f16`. Flash Attention is on by default |
| `--moe-cache-mib` | Described as experimental |

They are measured in P02 ([ADR 0030](../decisions/0030-koboldcpp-efficiency-levers.md)).

## Documentation checks

| Check | Result |
| --- | --- |
| Relative links and anchors in all Markdown files | See the commit that adds this file. The check script ran before the commit and reported no error |
| Every test ID used in a roadmap exit or in traceability exists in [Testing](../testing.md) | Same run |
| No model weights, tokens or private data added | `git status` and a review of the diff before the commit |
