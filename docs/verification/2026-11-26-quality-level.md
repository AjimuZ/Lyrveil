# Quality level smoke runs, 2026-11-26

Status: recorded 2026-11-26 for QLT.1, the first runs of `scripts\lyrveil.cmd test quality` on the reference machine.
Both were **smoke runs** (`short`) on one profile. They prove that the level, its preparation, its drivers and its
cleanup work against the real stack. They are **no evidence for the catalog**: neither the full T-QLT-05 nor the full
T-QLT-06 has run, on any profile, so no `quality_evidence` is set.

## What ran

Commands, from Git Bash in the repository root, one after the other. The consistency run was made twice: after the
first one, only names in the results changed (`last_process` lost its claimed unit of seconds), and the second confirmed
the final code:

```text
scripts/lyrveil.cmd test quality Extraction short rocinante-x-12b
scripts/lyrveil.cmd test quality Consistency short rocinante-x-12b
scripts/lyrveil.cmd test quality Consistency short rocinante-x-12b
```

- Machine: NVIDIA GeForce RTX 4070, 12282 MiB as read by `nvidia-smi` (632 and 638 MiB in use by other programs at the
  starts). Docker Desktop with Linux containers; `docker info` reported 24031.8 MiB.
- Pester 5.7.1 under Windows PowerShell 5.1. Images: the `:gpu-test` runtimes from the build cache, the test-only
  orchestrator, the backend's runtime stage and its test stage (`lyrveil/backend-test:gpu-test`) for the probe.
- Project `lyrveil-gpu-test`, data volume `lyrveil-gpu-test-data`, `lyrveil-models` read-only. Both runs ended with
  `[ok  ] quality: no container or volume of the test project is left`.

## T-QLT-06, the first 5 windows on `rocinante-x-12b`

`Pester 5.7.1: 2 passed, 1 failed`, then `test quality: FAILED (quality)` and exit status 1. Pester reported 51.86 s.

| Target | Value on the 5 windows | Target |
| --- | --- | --- |
| Expected items found | 0 of 5 (0.0) | At least 0.85 |
| Inferences stored as `stated` | 0 of 1 | At most 0.03 |
| Card changes under another kind | 0 (the first 5 windows hold no identity item) | 0 |
| Valid output after the repair | 5 of 5, none repaired | At least 0.98 |

Each window took one generation of 1.5 to 3.4 s. Under the job's own prompt, grammar, length (1200) and temperature
(0.3), the model returned an empty `memories` list for w02 to w05, a relationship event for w05, and for w01 one
`user_fact` with relation `duplicate` and `target_ref` `E1`, although w01 shows no existing memory; the job drops such an
item, and so did the probe. So the harness works, and the extraction prompt on this profile finds far too little: a
finding for the P04 tuning round (first templates and examples, then samplers), not a fault of the level. The worked
example of the prompt, which uses `E1` and an `update`, may be what the model copied.

## T-QLT-05, the first probe block on `rocinante-x-12b`

First run: `Pester 5.7.1: 6 passed, 0 failed`, then `test quality: passed` and exit status 0. Pester reported 291.8 s.
The short mode ran 4 messages per demo character and the C6 conversation of Nerea: 2 messages on `rocinante-12b-v1.1`,
then the backend changed back to `rocinante-x-12b` (PATCH `/settings/models` and **Applica ora**) for the other 2. The
table is this run.

| Check | Value | Target |
| --- | --- | --- |
| Replies completed | 16 of 16 | All |
| C1 | 12 of 12 probes right; no V6 or V10 hit delivered | At least 95 % and none |
| C2 | 0 V11 hits in 12 replies | At most 1 per 200 |
| C3 | 0 V13 hits | At most 2 per 100 |
| C4 | Median 13 tokens (`brevi`, Nerea), 63.5 (`lunghi`, Ilario), 9 (`brevissimi`, Tessa); no reply cut by N4 | At most 70, 300, 30; at most 10 % cut |
| C5 | 0 narration spans delivered, 0 removed by N3 | 0 |
| C6 | 2 of 2 probes right after the change; median 26.5 tokens | C1 and C4 met |

Second run: `Pester 5.7.1: 5 passed, 1 failed`, `test quality: FAILED (quality)`, exit status 1, 299.0 s, nothing left
behind. Every reply completed, and every step after the conversations ran too. C1 missed at 11 of 12 (0.9167): to "Che
lavoro fai?" Tessa answered "Apprendo pozione! 🧪💜", which her first occupation probe did not accept, although her
second occupation probe listed `pozion`. After this run the script gave every probe of the same topic of one character
the same fragments (Tessa's and Ilario's occupation, Nerea's main trait), and a unit case now checks that. With the
aligned list that answer counts as right. The run was not repeated after that data change, and replies vary from run
to run, so even the short mode is no stable score. SmartCache again kept 0: `last_process` 0.1704 with 0 and 0.1684
with 2 (1.2 %), peak 1347.6 MiB.

**SmartCache.** With the stack removed, `lyrveil-llm` ran alone at level A with `SMART_CACHE=0` and then `2`
(`LEVEL=CUSTOM`). One pair was replayed: the prompt of Nerea's third reply, the extraction prompt of window w01 under its
grammar, then the prompt of her fourth reply. `last_process` of that last prompt was 0.1724 with 0 and 0.1694 with 2,
a fall of 1.76 %, so the rule kept **0**. The runtime's peak was 1068 MiB with 0 and 1345.5 MiB with 2, which leaves
22686 MiB below the memory Docker sees. `last_process` is read in KoboldCpp's own unit; it behaves as milliseconds per
prompt token, as `last_eval` did in T-GPU-04, which keeps the ratio because both trials replay the same prompts. With a
history of three messages there was little for SmartCache to save. The full mode replays 10 pairs from the end of
a 120-message conversation, and only that run can decide. Neither catalog profile could carry 2 anyway: the catalog
validator allows it only for `min_tier` `target` (`catalog_allows_2` false).

**SWA padding.** Recorded as `not_applicable` for `rocinante-x-12b`: its template has no sliding-window attention
([ADR 0045](../decisions/0045-drop-gemma-4-rocinante-x-default.md)).

## What has not run

- The full T-QLT-05 (120 messages for each demo character and the C6 conversation, on both catalog profiles, with 10
  SmartCache pairs). At the speeds above it needs about one to two hours per profile.
- The full T-QLT-06 (40 windows) on either profile, and the plain `scripts\lyrveil.cmd test quality`, which runs both
  files on both profiles.
- `rocinante-12b-v1.1` as the measured profile. It served only as the first profile of the C6 conversation here.
- C7 and T-QLT-07, which need the owner. T-QLT-01 needs the owner too: see below.
- The Pester unit tests `tests/scripts/QualityLevel.Tests.ps1` and the new cases of `tests/scripts/TestCommand.Tests.ps1`.
  The orchestrator's `unit` run is their first. `backend/tests/unit/test_gpu_extraction_probe.py` passed (15 tests,
  `scripts/lyrveil.cmd test unit tests/unit/test_gpu_extraction_probe.py`).

## T-QLT-01 generation and blind sheet (QLT.2)

**What ran, on 2026-11-26.** `scripts/lyrveil.cmd test lint` passed (PSScriptAnalyzer 51 files, 0 findings). Then
`scripts/lyrveil.cmd test quality Consistency short rocinante-x-12b` ran the new generation code on the RTX 4070 in
301.8 s. Pester reported 6 passed and 2 failed. Both failures are findings about the model on a smoke run, not defects
of the harness: the hard targets of T-QLT-05 were `MISSED` (`HardPassed` false), and the new case on M1 to M4 failed on
M4, because the 12 replies of the short mode answer the four scripted identity probes of each character, which hold
almost no question (Nerea 0 of 4 against 0.30, Ilario 1 of 4, Tessa 0 of 4 against 0.50). M1 was 1.0, M2 had 0 hits and
M3 had no repeated lead. The project and the data volume were removed (`no container or volume of the test project is
left`), and the GPU memory was released.

**What it produced.** `test-results/quality/italian-short-20261126-213600/` with `sheet.md`, `key.json` and
`metrics.json`; the sheet named no profile and listed the three characters. The first version of the sheet put all 12
probe replies in Part 2 and none in Part 1, because the authorship sample took everything. The sampler now gives M6 at
most half of a character's replies; this was fixed after that run and **has not run on the real stack again**, only in
the unit tests, which have not run (below). With the probe replies, the sheet shows names and places, so it is a pipeline
check and no test of M6.

**What the import did.** `scripts/lyrveil.cmd test quality-rating` on that unfilled sheet read the key and the metrics
from JSON, printed `incomplete` for the profile with 12 of 12 answers missing, listed every blank id, wrote
`verdict.json` and exited 1. No filled sheet was imported: no owner has rated one, and no rating was invented to
exercise the import on the real files.

**Not run.** The unit tests `tests/scripts/ItalianQuality.Tests.ps1`, the new case in
`tests/scripts/QualityLevel.Tests.ps1` and the two `quality-rating` cases in `tests/scripts/TestCommand.Tests.ps1`: the
`unit` level runs every Pester file together, and was left to the orchestrator. They are written against synthetic
replies and ratings (boundaries of M1 to M4, M5 average 4.0 and 3.97, M6 at 16 and 15 of 20, blank values, a wrong
sheet id, two profiles). Also not run: the full generation on either profile (T-QLT-05's 120-message conversations, about
an hour or two per profile), the owner's rating, and so any T-QLT-01 verdict. The SWA padding measurement does not apply
to the catalog after ADR 0045.
