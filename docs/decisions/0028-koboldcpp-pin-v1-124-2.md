# ADR 0028: Locally built runtime images, with KoboldCpp pinned to v1.124.2

Status: accepted (2026-11-05). Supersedes: [ADR 0007](0007-own-runtime-images-pinned.md).

## Context
[ADR 0007](0007-own-runtime-images-pinned.md) pinned the asset `koboldcpp-linux-x64` of release `v1.124.1`. On 2026-11-05 the GitHub API returns `Not Found` for that release, and the asset URL returns 404. The tag `v1.124.1` still exists at commit `9cf05e3e2639195d7f3bee0bd53bfc25a0a7ea3e`. The current release is `v1.124.2` at commit `6d2fedf8624e5f05926af3191f9efdcb13c4133c`, and its notes include the 1.124.1 hotfix and improved sliding-window handling for Gemma 4. All other reasons of ADR 0007 still hold.

## Decision
- `docker/llm`: download `koboldcpp-linux-x64` from release `v1.124.2` (628,981,944 bytes), verify SHA-256 `c66ae0106673c7d9c41bafcc92c95bc5a6256709e9d853e5e2255bf536c1b069`, run `--unpack /opt/koboldcpp`, and start `koboldcpp-launcher`.
- `docker/image`: unchanged. ComfyUI at commit `426f5eea52a7ece1dd599fff4cb6cd1f352ddd24` (`v0.43.0`), with a hash-pinned lock file and PyTorch for CUDA 13.0.
- Images build on the user's machine during setup. Weights and generated data never enter them. `pull_policy: never` applies at runtime.
- If a pinned asset disappears again, the build fails on the hash or the download. The fix is a new pin, verified against [Runtime contracts](../runtime-contracts.md), never a moving tag.

## Alternatives
- Building KoboldCpp from the surviving `v1.124.1` tag: it needs a CUDA toolchain, for a release its author has withdrawn.
- Following the latest release automatically: moving contents and unverified flags.

## Consequences
The flags and endpoints in [Runtime contracts](../runtime-contracts.md) were checked again in `koboldcpp.py` at `v1.124.2`. P02 confirms them at runtime. Users who built the image before keep a working image, because nothing is pulled at runtime.
