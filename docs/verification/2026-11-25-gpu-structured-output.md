# Structured output with grammar on the GPU: T-GPU-05, 2026-11-25

Status: recorded 2026-11-25 for GPU.4, one run of the `gpu` level on the reference machine.

## What ran

Command, from Git Bash in the repository root:

```text
scripts/lyrveil.cmd test gpu StructuredOutput
```

- Machine: the reference machine, NVIDIA GeForce RTX 4070, 12282 MiB as read by `nvidia-smi`, 856 MiB in use by other
  programs before the load. Pester 5.7.1 under Windows PowerShell 5.1. KoboldCpp 1.124.2, as `/api/extra/version`
  reported.
- Only `tests/gpu/StructuredOutput.Tests.ps1` ran. The argument `StructuredOutput` is the file selector added with this
  task. The level's preparation and cleanup were the same as in a full run: the project `lyrveil-gpu-test`, its own
  data volume `lyrveil-gpu-test-data`, `lyrveil-models` mounted read-only, no running Lyrveil.
- The backend's test stage was built as `lyrveil/backend-test:gpu-test`. Every probe container ran with
  `--network none --read-only --cap-drop ALL` and was removed on exit.
- Profile: `rocinante-x-12b`, the default of the target tier, family `mistral_v3_tekken`, file
  `TheDrummer_Rocinante-X-12B-v1-Q4_K_M.gguf`, at launch level A: `CONTEXT_SIZE=12288`, `GPU_LAYERS=-1`,
  `BATCH_SIZE=512`, `MOE_CPU_LAYERS=0`, `USE_MMAP=0`, `KV_QUANT=f16`, `SWA_PADDING=0`, `SMART_CACHE=0`, `LEVEL=A`.
  It was ready in 33.2 s, with NVML at 10065 MiB after the load, and `/api/extra/true_max_context_length` gave 12288.
- Task: `character_draft`, template `character_draft.v1`. The grammar was `schema_to_gbnf(CharacterDraft)`: 13 rules,
  SHA-256 `d5508048f8a0623d2f87a0feec00fa5ec39f79cbc6543386375f4967a1a170b0`. The request had `max_length` 1500,
  temperature 0.4 and no sampler seed, as the job sends it. The job allows 2 attempts and 180 s per generation.
- The results file is `test-results/gpu/structured-output-20261125-223304.json`. Git ignores it, and it was not copied
  into the repository. It holds every output.

## Result

`Pester 5.7.1: 9 passed, 0 failed, 0 skipped, 0 not run`, then `test gpu: passed`. Pester reported 89.48 s for the
tests. Cleanup: `down --volumes --remove-orphans` and `docker volume rm --force lyrveil-gpu-test-data` ran, and the
level reported `no container or volume of the test project is left`.

| Description | Attempts | Accepted | Time | Tokens | T/s (request) | T/s (generated) | Age | Proposed fields |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `libraia` (41 years, bookseller) | 1 | yes | 11.31 s | 545 | 48.2 | 49.8 | 41 | 29 |
| `meccanico` (29 years, mechanic) | 1 | yes | 10.30 s | 506 | 49.1 | 49.2 | 29 | 31 |
| `astronoma` (35 years, researcher) | 1 | yes | 10.39 s | 513 | 49.4 | 49.4 | 35 | 30 |

- Every output under the grammar was a JSON object with the keys `identity`, `traits`, `communication` and `visual`, in
  order. The job's validation (`check_draft`: the draft schema, then `validate_card` with the content guard) accepted
  each one at the first attempt, so the repair path did not run on the real model. Each generation ended with the end
  token (`stop_reason` 1), well before `max_length`.
- "Time" is curl's `time_total` inside the container. "T/s (request)" is the generated tokens over that time.
  "T/s (generated)" is derived from `last_eval`, read as milliseconds per generated token (T-GPU-04).
- Without the grammar, the first description's prompt gave 512 tokens in 10.03 s, at 51.1 T/s generated. The job's
  validation refused that output as not JSON (`La risposta del modello non è un JSON valido.`). It was a JSON object
  followed by an Italian sentence about the answer. It also held `"initiative_frequency": "a volte"`, which the
  schema's enum (`a_volte`) does not allow and which the grammar rules out.
- The grammar's cost: a median of 49.4 T/s generated under the grammar against 51.1 T/s without it, so 3.3 %. The
  slowest request under the grammar took 11.31 s of the job's 180 s.

## What this shows and what it does not

- A-09 ([risks](../risks.md)) holds for this task on this profile. The schema grammar gave valid JSON with the
  schema's shape on every attempt, at about 3 % of the generation speed. The fallback to a generic JSON grammar is not
  needed.
- One task and one profile were measured. Memory extraction, summaries, proactive decisions, image scenes and the
  visual profile translation use other grammars, and none of them ran on the GPU. `rocinante-12b-v1.1` (`chatml`) did
  not run this test.
- Three drafts are a small sample. Every draft was accepted at the first attempt, so the repair prompt was not
  exercised on the real model; its unit and integration tests use the fake runtime.
- The draft went through `POST /api/v1/generate` with curl in the runtime container, not through the backend's
  scheduler and KoboldCpp adapter. The probe gave the same request fields and judged the output with the same
  function as the job.
- The quality of the Italian text in the drafts was not judged. That is the P04 quality protocol.
- `tests/scripts/TestCommand.Tests.ps1`, with its new case for the file selector, and
  `backend/tests/unit/test_gpu_structured_probe.py` belong to the `unit` level. Only the probe's pytest file ran here
  (`scripts/lyrveil.cmd test unit tests/unit/test_gpu_structured_probe.py`: 7 passed).
