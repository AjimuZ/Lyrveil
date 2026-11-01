# ADR 0018: Adult-only characters and deterministic content safeguards

Status: accepted (2026-11-01). Supersedes: none.

## Context
The brief does not cover this topic. The product simulates relationships, with a roleplay finetune whose card hints at mature output, and an image model that accepts Danbooru-style tags. The Illustrious license forbids exploiting or harming minors, and laws in many countries forbid sexual content involving minors, including fictional content. A public project must have a firm position, and these rules cannot depend on the model obeying a prompt.

## Decision
- **Adults only.** `age` is required and at least 18, enforced by the schema, a database `CHECK`, import and drafts.
- **Content guard.** Deterministic term lists in Italian and English refuse minors in character data and image requests, and refuse sexual content involving minors in generated text. Text is checked sentence by sentence during streaming.
- **Image safety.** `adult` in the positive prompt, and `child, loli, shota, toddler, underage, minor, nsfw, explicit, nude` always in the negative prompt. No explicit-content mode in 1.0.
- **Real people.** No face-swap or likeness feature. Uploads carry a notice about rights and consent.
- **Honesty.** A clear notice says characters are simulated, and a character does not deny it when sincerely asked.
- **Wellbeing.** When the user appears to be in danger or talks about self-harm, the character leaves the fiction gently and encourages real help. Proactive messages never induce guilt.
- Settings cannot disable these rules.

## Alternatives
- Relying only on model behavior and license terms: not reliable.
- A classifier model for moderation: excluded, because only two models are allowed.
- An optional explicit mode for adults: out of the brief's scope, and it would raise the risk of the filters above failing.

## Consequences
Term lists are best-effort, and can produce false positives such as an adult character's "liceo" memories, or miss a phrase. Lists live in the repository and improve through tests. Documents state clearly that these are safeguards rather than guarantees. Forks are bound by the licenses and the law, whatever they change.
