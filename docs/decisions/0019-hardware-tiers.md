# ADR 0019: Target and reduced hardware tiers

Status: accepted (2026-11-01). Supersedes: none.

## Context
The brief targets 32 GB of RAM. The reference machine measured on 2026-11-01 has one 16 GB module (15.8 GiB visible), and with no `.wslconfig`, Docker sees 7.64 GiB. ComfyUI reads a 6.94 GB checkpoint while it holds Python and PyTorch in memory, and the planning estimate for its peak is 10 to 11 GiB. Fully offloaded KoboldCpp needs much less system RAM, but it also reads a 7.48 GB file. Only one of the two runs at a time.

## Decision

| Tier | Windows RAM | WSL2 `memory` | WSL2 `swap` | Support |
| --- | --- | --- | --- | --- |
| Target | 32 GB | 24GB | 8GB | Full |
| Reduced | 16 GB | 12GB | 16GB | Supported with a warning. Close heavy programs during image generation |
| Below | Under 16 GB | | | Not supported |

Preflight reads the memory Docker sees: at least 20 GiB passes, 11 to 19.9 GiB warns, under 11 GiB fails with the exact `.wslconfig` to apply. `LYRVEIL_ALLOW_LOW_MEMORY=true` turns the failure into a warning, and is unsupported. Scripts never edit `.wslconfig`, because it is a Windows-wide setting and needs `wsl --shutdown`.

## Alternatives
- Requiring 32 GB: it would exclude the actual reference machine.
- Editing `.wslconfig` automatically: a system setting that affects everything in WSL2.
- Lowering image quality for low RAM: RAM mainly affects loading rather than resolution, and tiled decode already handles VRAM.

## Consequences
Development can proceed on the reference machine after one configuration step. The P05 and P06 measurements replace the planning estimates. If the reduced tier proves unusable, the decision is to raise the minimum and record it in a new ADR. Results from the target tier cannot be claimed until a machine with 32 GB runs them.
