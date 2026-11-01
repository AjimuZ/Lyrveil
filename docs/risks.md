# Risks, assumptions and open questions

Status: reviewed 2026-11-01.

## Open questions

None. Every question raised during design has an accepted decision in [the ADRs](decisions/README.md). What can only be known by running the system has a default, a measurement and a fixed rule for what to do with the result.

## Real blockers

None for implementation. Two conditions must be met on the reference machine first, and both are one-time setup:

| Condition | Measured | Action |
| --- | --- | --- |
| Docker needs at least 11 GiB | 7.64 GiB | Apply the 16 GB [`.wslconfig`](operations.md#wsl2-memory) |
| A Linux distribution is required for the scripts | Only `docker-desktop` | `wsl --install -d Ubuntu-24.04`, then clone inside it |

## Assumptions

Each assumption is verified by a named check. A failure triggers the stated decision, so none stays open.

| ID | Assumption | Verified by | If false |
| --- | --- | --- | --- |
| A-01 | Rocinante with 8192 context fits in VRAM with at least 512 MiB of margin | T-GPU-02 | Profile B becomes the default. Autofit keeps it working meanwhile |
| A-02 | ChatML works well as plain text on this model, with text stop sequences | T-GPU-03, T-GPU-04 | Stay on ChatML if replies are clean. Otherwise switch to the Mistral format, which the author also lists, through a new template version |
| A-03 | The unpacked KoboldCpp launcher runs on a read-only root | P02.1 | Keep the single-file binary with an exec tmpfs |
| A-04 | ComfyUI runs with `cap_drop: ALL` and a read-only root | P05.1 | Add the smallest tmpfs or capability needed and record it |
| A-05 | SDXL at 1024 x 1024 fits in the VRAM left after the desktop | T-GPU-06 | Tiled decode is the default for that machine |
| A-06 | A full mode switch takes under 150 s | T-GPU-07 | Keep the design and improve the wait messages. Batching rules limit how often switches happen |
| A-07 | Rocinante's Italian reaches the quality targets | T-QLT-01 | Improve templates and examples, then samplers. Record the limit in the README. No model change without the owner's authorization |
| A-08 | FTS5 with Snowball stemming reaches recall at 8 of 80 % | T-MEM-22 | Tune weights and stop words. Record the result. Embeddings need a new ADR |
| A-09 | GBNF grammar gives valid JSON with acceptable speed | T-GPU-05 | Generic JSON grammar plus validation and repair |
| A-10 | Group 0 lets a non-root user use the Docker socket on Docker Desktop | P01.4. The socket's mode was measured | Run that one container as user 0, with all capabilities dropped |
| A-11 | Docker `internal: true` networks work with `--gpus` on Docker Desktop | T-DCK-08, T-GPU-01 | Keep the network and add an egress check. Never publish a runtime port |

## Risk register

Owner for all rows: the project maintainer.

| ID | Risk | Likelihood | Impact | Mitigation | Response if it happens |
| --- | --- | --- | --- | --- | --- |
| R-01 | Italian sounds artificial, because the finetune states no Italian support | Medium | High | Quality protocol, examples per character, calque list, DRY | Tune and record. Accept the documented limit |
| R-02 | 16 GB of RAM causes swapping or failures in image mode | Medium | Medium | Reduced tier, `.wslconfig`, swap, `ADR 0019` | Tiled decode. Advise closing programs. Upgrade RAM to the 32 GB target |
| R-03 | Rocinante's license terms are unclear | Medium | Medium | Never redistribute, acceptance step, clear notice | Stop recommending it if terms become incompatible. Any replacement is the owner's decision |
| R-04 | NVML gives partial data on WSL2 | Known | Low | Docker is the authority. NVML is only a memory gauge | Show unknown values as unknown |
| R-05 | Other programs hold VRAM | High | Medium | Autofit, thresholds, blocked state with a clear message | Wait, inform, never touch them |
| R-06 | A socket compromise gives control of Docker | Low | Critical | [Controls](security.md#docker-socket) | Rotate token, rebuild, review. Residual risk accepted |
| R-07 | Upstream runtime breaks an API | Medium | Medium | Pinned versions, contract document, recorded fixtures | Stay pinned until re-verified |
| R-08 | A pinned download disappears upstream | Low | High | Pinned revisions and hashes. Users keep local copies | Update the pin after verification |
| R-09 | Mode switching feels slow | High | Medium | Batching, honest status, text queue | Measured targets and improved messages |
| R-10 | Prompt changes reduce cache reuse and slow replies | Medium | Medium | Prefix stability, selection bonus, coarse time values | Measure `last_process`, and tune |
| R-11 | Models generate unwanted content | Medium | High | Global rules, content guard, safety negatives, license terms | Add terms to lists. Guards remain best-effort |
| R-12 | The product encourages unhealthy attachment | Low | High | Clear simulation notice, proactive off by default, no guilt, leaving fiction on danger | Reinforce the rules in templates |
| R-13 | Private data enters Git | Low | Critical | Ignore rules first, synthetic fixtures, audit | [Publishing](publishing.md#never-commit) procedure |
| R-14 | Docker Desktop updates change GPU behavior | Medium | Medium | Preflight on each start | Record the working versions in verification notes |
| R-15 | User loses data by removing the WSL distribution | Low | High | Backups, documented data location | Restore from backup |
| R-16 | Memory extraction stores wrong facts | Medium | Medium | Provenance, labels, Memory Studio, tombstones | The user corrects. Quality tests add the case |
| R-17 | SQLite corruption or a disk failure | Low | High | WAL with `synchronous=FULL`, Linux filesystem only, backups | Restore from backup |
| R-18 | Characters drift in long chats | Medium | Medium | Identity block never cut, canonical protection, validators | Tune templates, add examples |
| R-19 | Image consistency disappoints | High | Low | Persistent tags, locks, honest documentation | Prepared extensions after 1.0 |
| R-20 | Angular or dependency versions move on | High | Low | Lock files. Angular 22 is active until 2027-06 | Upgrade in a dedicated change after 1.0 |
