# ADR 0034: Consistency protocol and verified profiles

Status: accepted (2026-11-06). Supersedes: none.

## Context
- The [Italian quality protocol](../prompting.md#italian-quality-protocol) (T-QLT-01) measures language quality on three profiles. Nothing measures whether a character keeps its name, age, gender, register and length on each profile, or after a profile change in the middle of a conversation.
- Memory extraction and summaries run on whatever profile is active. A weaker profile can store an inference as a fact, which then misleads every later reply. Nothing measures extraction quality per profile.
- Two documents disagree about custom profiles:
  - [Model catalog](../models.md#custom-profiles) says they use "the generic sampler preset".
  - [Prompting](../prompting.md#sampler-presets) says they use the preset of their family, and defines no generic preset.

## Decision
- **Consistency protocol T-QLT-05.** The scripts live in `tests/quality/consistency/`. P04 runs them on every catalog text profile, and P08 runs them again on the default. Each of the three demo characters runs a scripted conversation of 120 messages.

  | Check | Target | Hard target |
  | --- | --- | --- |
  | C1. Identity probes: 4 questions at each of messages 10, 60 and 120, so 12 per character. They cover name, age, occupation, place, gender, a main trait and a fact from block 2, checked with keyword lists | At least 95 % correct, and no wrong name or age delivered | Yes |
  | C2. V11 hits delivered after the corrective generation | At most 1 per 200 replies | Yes |
  | C3. V13 hits | At most 2 per 100 replies | No |
  | C4. Median reply length per band, and replies cut by N4 | Median within the band target, at most 10 % cut | Yes |
  | C5. Narration spans delivered for `nessuna` | 0. The model's raw tendency is recorded | No |
  | C6. A profile change at message 60 | C1 and C4 still met after the change | Yes |
  | C7. Owner's blind test: which character wrote each of 20 replies | At least 80 % correct. Run on the default and the two roleplay profiles | No |

- **Extraction evaluation T-QLT-06.** The scripts live in `tests/quality/extraction/`. P04 runs them on every catalog text profile, with 40 synthetic windows of expected items. Targets:
  - at least 85 % of the expected items are found, with the right kind and provenance;
  - at most 3 % of inferences are stored as `stated`;
  - no change to canonical data, outside `identity_candidate`;
  - at least 98 % valid output after the repair attempt.
- Each structured template gets one worked example. The example stays only if it does not lower the T-QLT-06 score.
- **Verified profiles.** Each catalog text profile gets one tuning round: first templates and examples, then samplers. A profile that still misses a hard target of T-QLT-05, or any target of T-QLT-06, leaves the catalog through a new ADR. Its file can still be registered as a custom profile.
- The default must pass T-QLT-01, T-QLT-05 and T-QLT-06.
- The catalog gains `quality_evidence`. It holds the path of the verification note that records the passed protocols, or null. The settings show **Qualità verificata** only when it is set.
- A **`generic`** sampler preset, with the `nemo` values, serves every custom profile, whatever its family.

## Alternatives
- **Trust the model cards.** None of them describes Italian persona consistency.
- **Run the protocol on the default only.** The user may choose any profile, and the requirement is consistency on all of them.
- **An automatic judge model.** R-MOD-06 allows one text model, and a model grading itself is no evidence.

## Consequences
- P04 grows by about one evening of runs per profile, plus an owner session for C7.
- The catalog may shrink after P04, with the evidence in `docs/verification/`.
- Profiles that pass carry a visible badge. Custom profiles stay possible, marked **non verificato**.
