# ADR 0007: Locally built runtime images from pinned versions

Status: superseded by [ADR 0028](0028-koboldcpp-pin-v1-124-2.md) on 2026-11-05. Accepted 2026-11-01. Supersedes: none.

## Context
KoboldCpp's README says that its Docker image `koboldai/koboldcpp` "is intended for experts only", primarily for cloud GPU rentals. ComfyUI's README documents no Docker image. Offline use, reproducibility and licensing all argue against moving tags and runtime updates.

## Decision
- `docker/llm`: download `koboldcpp-linux-x64` from `v1.124.1`, verify SHA-256 `46740ff67194962d50c564ab3d01a41d83e2cb3f29073915f83ebac21399e8e5`, run `--unpack /opt/koboldcpp`, and start `koboldcpp-launcher`.
- `docker/image`: clone ComfyUI at commit `426f5eea52a7ece1dd599fff4cb6cd1f352ddd24` (`v0.43.0`) and install a hash-pinned lock file with PyTorch for CUDA 13.0.
- Images build on the user's machine during setup. Neither weights nor generated data enter them. `pull_policy: never` applies at runtime.

## Alternatives
- Upstream or community images: moving contents, possible runtime downloads, unclear support.
- Building KoboldCpp from source: slower, and needs a CUDA toolchain, for no benefit.
- Running the single-file binary each start: it would extract itself on every model switch.

## Consequences
Setup takes longer and downloads several gigabytes once, then runs offline. Upgrades are explicit changes that re-verify [Runtime contracts](../runtime-contracts.md) and the GPU tests. P02 and P05 confirm the launcher and the read-only root, with fixed fallbacks in [Risks](../risks.md#assumptions). Not publishing images avoids AGPL and GPL distribution duties for the project.
