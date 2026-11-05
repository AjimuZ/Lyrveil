# ADR 0027: Hardware tiers after the reference machine's RAM upgrade

Status: accepted (2026-11-05). Supersedes: [ADR 0019](0019-hardware-tiers.md).

## Context
On 2026-11-01 the reference machine had one 16 GB module, and [ADR 0019](0019-hardware-tiers.md) supported it as a reduced tier. On 2026-11-05 it has two Crucial DDR5-5600 modules of 16 GB, 31.77 GiB visible. Without `.wslconfig`, Docker sees 15.50 GiB, which is in preflight's warning band. With 32 GB, a mixture-of-experts text model can keep part of its weights in system RAM ([ADR 0024](0024-selectable-model-profiles.md)).

## Decision

| Tier | Windows RAM | WSL2 `memory` | WSL2 `swap` | Default text profile | Support |
| --- | --- | --- | --- | --- | --- |
| Target | 32 GB | 24GB | 8GB | `gemma-4-26b-a4b` | Full. The reference machine |
| Reduced | 16 GB | 12GB | 16GB | `gemma-4-12b` | Supported with a warning. Close heavy programs during image generation |
| Below | Under 16 GB | | | | Not supported |

- Preflight is unchanged: at least 20 GiB seen by Docker passes, 11 to 19.9 GiB warns, under 11 GiB fails with the exact `.wslconfig` to apply. `LYRVEIL_ALLOW_LOW_MEMORY=true` turns the failure into a warning and is unsupported.
- `setup.sh` picks the default text profile from the memory Docker sees: 20 GiB or more selects the target default. The user can change it later.
- Profiles that need the target tier are refused on a smaller tier, with a message that names the memory needed.
- Scripts never edit `.wslconfig`.

## Alternatives
- Keep the reduced tier for the reference machine: it would leave the new RAM unused.
- Use the 26B-A4B profile on every tier: with 12 GB for WSL2, the experts kept in RAM and the page cache would compete with ComfyUI.

## Consequences
The reference machine needs the 32 GB `.wslconfig` before P01. Target-tier results can now be measured on it, and the reduced tier is no longer measured on the reference machine. Its figures stay estimates until someone runs the tests on a 16 GB machine, and the verification notes say so.
