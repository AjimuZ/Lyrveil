# ADR 0045: Drop the Gemma 4 profiles and make Rocinante X 12B the default on every tier

Status: accepted (2026-11-25). Supersedes, in part: [ADR 0024](0024-selectable-model-profiles.md) (the Gemma 4 text
profiles and the Gemma 4 target default) and [ADR 0027](0027-hardware-tiers-after-ram-upgrade.md) (the default text
profile of each tier). Owner decision of 2026-11-25.

## Context
- [ADR 0024](0024-selectable-model-profiles.md) put three Gemma 4 based text profiles in the catalog:
  `gemma-4-26b-a4b` (target default), `gemma-4-12b` (reduced default) and the finetune `orion-26b-a4b`.
  [ADR 0027](0027-hardware-tiers-after-ram-upgrade.md) named the two Gemma 4 models as the tier defaults.
- On 2026-11-25 the owner decided that Gemma 4 no longer convinces them, and asked to remove every Gemma 4 based text
  profile and make `rocinante-x-12b` the default text profile on every tier. The supervisor deleted the three Gemma
  files from the `lyrveil-models` volume; only `rocinante-x-12b` and `rocinante-12b-v1.1` remain on disk.
- GPU.3 measured `rocinante-x-12b` on the reference machine (RTX 4070, 12282 MiB) on 2026-11-25
  ([evidence](../verification/2026-11-25-gpu-tuning.md)). The procedure's decisions: level A, context 12288 (margin
  2226 MiB at 12288), batch 512, `MOE_CPU_LAYERS=0`, `USE_MMAP=0`, `KV_QUANT=f16`. Its trial A at 8192 met the speed
  rule: a median of 53.0 tokens per second over 20 replies and a first token in 2.23 s for a 6000-token prompt.

## Decision
- `config/model-catalog.json` holds two text profiles, `rocinante-x-12b` and `rocinante-12b-v1.1`, and the three image
  profiles. `gemma-4-26b-a4b`, `gemma-4-12b` and `orion-26b-a4b` leave the catalog.
- `rocinante-x-12b` is the default text profile on both tiers, with the GPU.3 launch values: context 12288, batch 512,
  `use_mmap` false, `kv_quant` `f16`, `moe` false. `rocinante-12b-v1.1` has the same architecture (Mistral Nemo 12B, a
  Q4_K_M file 4,000 bytes smaller) and takes the same values, as the procedure's rule for unmeasured profiles says; T-GPU-02 checks
  its margin at those values.
- The backend's fixed reply settings, which stand in for the settings store until prompts follow the active profile,
  use the `mistral_v3_tekken` family and the `nemo` chat preset of [Prompting](../prompting.md#sampler-presets). The
  structured tasks keep their temperature of 0.3, the same in both presets.
- The `gemma4` sampler preset is removed from the catalog schema: only catalog profiles name a preset, and none uses
  it. A custom profile always gets `generic`.
- The `gemma4` template family stays: its formatter, its stop sequences, its V2 cut, its tokenization fixture recorded
  with `gemma-4-12b` by GPU.2, and its offer in **Aggiungi file locale**. Reason: a user may still register a Gemma 4
  file as a custom profile, and custom profiles registered with `gemma4` before this decision must keep a valid
  template. Such a profile has no catalog profile of its family, so it takes the fixed fallback launch values
  (context 8192, batch 512, `f16`). The `swa_padding` validator rule, which allows padding only for `gemma4`, stays
  for the same reason; every shipped profile has 0.
- The parts of the P02 measuring procedure that only a Gemma 4 profile needed are not applicable while no catalog
  profile is of that kind:
  - step 7, the comparison of AutoFit with `MOE_CPU_LAYERS` 8, 12 and 16, and its fallback default: both profiles
    are dense, so `MOE_CPU_LAYERS` stays 0 and T-GPU-14 applies the speed rule to trial A of the default;
  - the AutoFit and CPU expert layer analysis of the 26B profiles;
  - SWA padding of [ADR 0031](0031-stable-prefix-dynamic-context-and-anchor.md) and its P04 measurement, which serve
    only the sliding-window layers of Gemma 4.
  The code of step 7 stays in `scripts/lib/GpuTuning.psm1`, keyed by the catalog's `moe` flag and unit-tested with
  synthetic profiles, so a future mixture-of-experts profile is measured without new code.
- The gpu level measures `rocinante-x-12b` in the tuning procedure and loads both Rocinante profiles in T-GPU-02 to
  T-GPU-04. The tuning procedure's version becomes 2, so records measured at the old level A (context 8192) are not
  reused.

## Alternatives
- Keep the Gemma 4 profiles in the catalog but not as defaults: refused by the owner, and their files are gone from
  the volume, so they would be listed as missing on every machine.
- Make `rocinante-12b-v1.1` the default: it is the brief's model, but `rocinante-x-12b` is its successor on the
  instruct base and the profile GPU.3 measured.
- Remove the `gemma4` template family too: it would break custom profiles already registered with it, which would need
  a migration, and it would take away a working format for a file the user may still own.
- Keep context 8192 for `rocinante-x-12b`: the procedure measured 2226 MiB of margin at 12288, above the 1024 MiB that
  the context step needs, so its rule grows the context.

## Consequences
- A fresh setup downloads 14.42 GB on both tiers (the default text file and the default image file), against 21.38 GB
  and 13.91 GB before.
- Replies, drafts, memories and summaries are prompted in the format of the default profile. A `chatml` profile, or a
  custom profile of another family, is still not prompted in its own format until prompts follow the active profile.
- The Italian quality protocol of P04 runs on the two Rocinante profiles. Neither card lists Italian by name; Mistral
  Nemo lists it among its languages.
- The verification notes of 2026-11-25 keep their Gemma 4 figures as the record of what ran then.
- The kept values are the catalog's own level A, so the gpu level loads them as they ship. Measured on 2026-11-25
  ([evidence](../verification/2026-11-25-rocinante-default-gpu.md)): both profiles keep about 2210 MiB of margin at
  12288, and T-GPU-14 passes for `rocinante-x-12b` at 12288 (53.0 tokens per second, first token in 2.22 s).
- Open question for the owner: run again from 12288, step 5 of the procedure allows a second context step, to 16384
  (1580 MiB of margin). The catalog stays at the GPU.3 value of 12288 until the owner decides.
