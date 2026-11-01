# ADR 0020: Proactive messages off by default, missed events skipped

Status: accepted (2026-11-01). Supersedes: none.

## Context
The requirements make proactive messages configurable and off by default. They forbid simulating generation that never happened while the PC was off, and they require an explicit policy for missed events. A character that writes first can be pleasant, or it can feel like pressure.

## Decision
- Two switches, both off by default: global and per conversation.
- Limits: at least 6 hours between messages, 2 per day per conversation, 6 per day globally, no messages between 22:30 and 08:30, silence after 2 unanswered messages.
- A `decide` stage can refuse to send when there is no real reason. Scheduled messages requested by the user skip it.
- Templates forbid guilt and pressure.
- Missed event default `skip`. Options `deliver_once` and `next_window`. Real timestamps always.
- Proactive jobs never change the GPU mode.

## Alternatives
- Default on: intrusive, and it contradicts the requirements.
- Random timing: cheap, but it produces empty messages. The requirements forbid random phrases.
- Delivering all missed events at startup: floods the user and falsifies time.

## Consequences
New users never get unsolicited messages until they opt in. Some missed messages never exist, which is honest. Long unused conversations stay silent. Frequency depends on the character's `initiative_frequency`, computed with stable values, so tests are repeatable.
