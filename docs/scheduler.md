# Proactive messages and time

Status: accepted (2026-11-01), revised 2026-11-06 for the prompt layout of ADR 0031. Implementation: in progress, phase P07.
Delivered with P07.1: the `scheduled_events` table, the evaluation every 60 seconds, the limits, and the creation of the
`inactivity_check`, `follow_up` and `contextual_initiative` events (`backend/src/lyrveil/scheduling/`). Delivered with
P07.2: the `proactive_message` job handler (`ProactiveMessageService`) with the `decide` and `generate` stages and the
repetition check. Delivered with P07.3: the missed-event policies at startup (`SchedulerService.resolve_missed`) and the
stored `missed_events` notification. Not delivered yet: the time context (P07.4), the rest of the notifications (P07.5),
`scheduled_message` events made by the user (the handler already serves them), recurrence rules, and the API and screens
that switch the feature on. Until then the two switches can only be changed in the database, and both are off.

How P07.3 settles points the text leaves open:

- The step runs once, before the first 60-second pass, in the same process. It does nothing while `proactive.enabled` is
  off, and it leaves the `pending` events of a conversation whose own switch is off as they are. Each event follows the
  `missed_policy` it was created with, not the conversation's current one.
- "Missed" is read literally: any `pending` event with `due_at` more than 10 minutes old at startup, including one the
  limits held back (quiet hours, interval) while the backend was running. The notification text therefore says the
  events were overdue at startup, not that the computer was off.
- `skip` ends the event `missed` with the reason `policy_skip`. Because an `inactivity_check` or `follow_up` event
  counts as created in any status, a skipped one is not made again for the same silence or thread.
- `deliver_once` keeps the newest event (by `due_at`) of each conversation `pending` and sets its `due_at` to now, so the
  normal evaluation queues it at the next moment the limits allow and a second startup within 10 minutes does not count
  it again; the others end `missed` with the reason `newer_missed_event`.
  Only events whose own policy is `deliver_once` compete. The step itself writes no message: the message is written by the
  job, with the real time of its generation.
- `next_window` sets `due_at` to now when the local time is already inside a window, and otherwise to the earliest
  window start after now (`rules.next_window_start`), using the conversation's current windows.
- The notification is one `missed_events` row for the whole step, with the number of overdue events found, whatever their
  policy, in its Italian `body` and no conversation, message or topic. It is stored unread; showing it is P07.5's.
- A `running` event whose job row is gone, or whose job is no longer `queued` or `running`, ends `failed` with the reason
  `interrupted`: nothing would finish it, and a `running` event blocks its conversation.
- Recurrence: `recurrence_json` has no defined rule yet, so "a recurring event computes its next occurrence from the
  current time" waits for the step that lets the user create scheduled messages.

How P07.2 settles points the text leaves open:

- Both stages run in the job's first stage (`decide`), since both need `llm`: the handler ends the job there instead of
  moving it to `generate`, so a retry after a failure while writing decides again.
- The message is written whole, with no streaming and no typing indicator: the client gets one `message.completed`. The
  generations before it are invisible, so a discarded one needs no `message.restarted`.
- The `decide` prompt shows the relationship state lines, up to 6 memories found with the event's note and the newest
  messages, the open threads of the state and, for a `follow_up`, the thread as the note. `hook_ref` is read and checked
  but not stored. The time context joins it in P07.4.
- Before it starts, the run checks that both switches are still on and that the chat is active; otherwise the event ends
  `skipped` (`switched_off`) or `cancelled` (`conversation_unavailable`) and nothing is generated.
- A `skipped` event keeps its reason in `outcome_reason`: the model's own for `send: false`, otherwise `repetition`,
  `empty` or `blocked` (a V4 hit). Two empty texts and a V4 hit end the event the same way, since there is no reply
  bubble to hold a failure notice. A failure the job will not retry ends the event `failed` with the job's error code;
  a cancelled job ends it `cancelled`.
- The repetition check compares the lowercase, accent-free words of the text with each of the last 20 proactive
  messages of the same chat that are not deleted (difflib's ratio, threshold 0.8). It runs after the normalizer, so
  what it compares is what would be delivered.
- A `delivered` event keeps the `updated_at` it got when it began to run, so the limits count the message from the
  moment its job was queued, not from the end of the generation.
- `messages.scheduled_event_id` stays a plain column, without a key: SQLite adds one only by rebuilding `messages`.

How P07.1 settles points the text leaves open:

- A message counts against the limits from the moment its event is `running`, and stays counted as `delivered`. The
  unanswered count is the sent messages after the user's last live message (all of them when the user never wrote).
- The caps count the local day in `LYRVEIL_TZ`. A window whose `from` is after its `to` crosses midnight; `from` is
  included and `to` excluded.
- `inactivity_check` is created once per silence: one event after the last message covers it. `follow_up` is created once
  per thread, for threads whose `valid_until` passed in the last 7 days. `contextual_initiative` slots older than
  10 minutes are not created, because nothing is simulated for a time the backend was off.
- At most one job per conversation at a time, for the oldest due event whose kind is enabled. A held-back event stays
  `pending` and is looked at again on the next pass.
- Deleting a conversation sets its `pending` events to `cancelled`.

Characters can write first. The feature is off by default, both globally and for each conversation
([ADR 0020](decisions/0020-proactive-messages-default-off.md)). A message is sent only when there is a real reason drawn from the
character's memory and state.

## Event kinds

| Kind | Created by | Trigger |
| --- | --- | --- |
| `scheduled_message` | User | A chosen date and time, or a recurrence. The note says what it is about |
| `inactivity_check` | Scheduler | No interaction for `inactivity_hours` |
| `follow_up` | Scheduler | An active `open_thread` memory with `valid_until` has passed. The event falls in the next allowed window |
| `contextual_initiative` | Scheduler | `initiative_frequency` of the character's profile |

## Per-conversation settings

Stored in `conversations.proactive_json`.

| Setting | Default | Range |
| --- | --- | --- |
| `enabled` | `false` | |
| `min_interval_minutes` | 360 | 60 to 10080 |
| `max_per_day` | 2 | 1 to 6 |
| `max_unanswered` | 2 | 1 to 5 |
| `windows` | `[{"from": "08:30", "to": "22:30"}]` | Local time |
| `inactivity_hours` | 24 | 1 to 720 |
| `kinds` | All four | Subset |
| `missed_policy` | `skip` | `skip`, `deliver_once`, `next_window` |

Global settings:

| Setting | Default |
| --- | --- |
| `proactive.enabled` | `false` |
| `proactive.max_per_day_global` | 6 |

`initiative_frequency` sets how often `contextual_initiative` is evaluated: `raramente` once a week, `a_volte` twice a week, `spesso` four times a week.
These never exceed the limits above. The day and hour are computed from the conversation's hash and the week, so they are stable and do not depend on a random
number generator. After `max_unanswered` messages with no answer, the conversation stays silent until the user writes.

## Evaluation

`SchedulerService` runs every 60 seconds inside the backend process. If the backend is not running, nothing is evaluated.

For each conversation with proactive messages enabled:

1. Create due events for the triggers above.
2. Skip when any limit applies: quiet hours, interval, daily caps, unanswered limit.
3. Postpone by 10 minutes when the user was active in the last 2 minutes.
4. Create a `proactive_message` job.

The job has priority 50, runs only in `LLM_READY` and never causes a mode change. During image work it simply waits.

## Generation flow

**Stage `decide`.** The model receives the character's state, top memories, open threads, time context and, for `scheduled_message`, the note:

```python
class ProactiveDecision(BaseModel):
    schema_version: Literal[1]
    send: bool
    reason: Annotated[str, StringConstraints(max_length=200)]
    hook_ref: str | None = None      # Memory or thread that motivates the message
```

When `send` is false, the event becomes `skipped` with the reason, and no message exists. `scheduled_message` skips this stage, because the user asked for it.

**Stage `generate`.** The normal `ContextBuilder` runs without a current message
([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)). The stable system text and the history are the
same as for a reply, so they come from the cache. The last user turn holds only the frame of the dynamic context: blocks 4,
5 and 9, then this instruction, with no `[Messaggio di …]` line:

```jinja
Sei tu a scrivere a {{ user.display_name }}: non stai rispondendo a un suo messaggio.
Motivo: {{ reason }}
Scrivi un messaggio breve, naturale e coerente con quello che sai.
Non rimproverare {{ user.display_name }} per il silenzio e non usare frasi che mettono in colpa.
Non inventare eventi accaduti mentre non vi sentivate che contraddicano la tua storia.
```

The wording carries no grammatical gender, so it never contradicts `grammatical_gender`. A proactive message uses the
`brevi` [length band](prompting.md#length-bands), or the character's band when it is shorter, for both `max_length` and the
anchor's length text, so the anchor and the instruction agree.

**Checks.** The [reply normalizer](consistency-engine.md#reply-normalization) and the
[reply checks](consistency-engine.md#reply-checks) run as for a reply, including one corrective regeneration. A message
with similarity of at least 0.8 to one of the last 20 proactive messages is discarded and regenerated once. A second hit
marks the event `skipped`.

**Delivery.** The message appears as normal. `created_at` is the real generation time. `scheduled_event_id` links it to its event. A `notification.created` event and an unread badge follow.

## Time context

The backend computes it per request, in `LYRVEIL_TZ` (default `Europe/Rome`).

| Field | Example |
| --- | --- |
| Local date and weekday | `sabato 1 novembre` |
| Local time | `18:40` (for scheduling and diagnostics) |
| Part of day, for the prompt | `di sera` |
| Last interaction | `2026-10-29T21:10:00Z` |
| Elapsed time, for the prompt | `tre giorni fa` |
| Planned events | From `scheduled_events` and open threads with dates |
| Open conversations | Threads in `relationship_states` |

Delivered in P07.4: the date, part of day and elapsed-time lines of [block 4](prompting.md#block-4-state-and-time), for
replies and proactive messages (T-SCH-07). The planned events and open conversations rows are not in the prompt yet.

Prompts use coarse values, which keeps the prompt prefix stable. Part of day: `di notte` 00 to 05, `di mattina` 06 to 11, `a mezzogiorno` 12 to 13, `di pomeriggio` 14 to 17, `di sera` 18 to 23.

## Missed events

The scheduler runs only while local services are active. If the PC is off, no generation happens and none is simulated later.

At startup, an event with `due_at` older than 10 minutes is missed. Its policy decides:

| Policy | Behavior |
| --- | --- |
| `skip`, default | Status `missed`. Nothing is sent. |
| `deliver_once` | Only the newest missed event per conversation becomes a job, at the next allowed moment. The others become `missed`. The message has the real timestamp, and the character can mention elapsed time naturally. |
| `next_window` | `due_at` moves to the start of the next allowed window. |

Rules:

- A message's timestamp is never backdated.
- Conversations do not receive invented messages for hours when the PC was off.
- A recurring event computes its next occurrence from the current time.
- When events were missed, one `missed_events` notification reports the count. It has no invented content.

## Notifications

Notifications are in-app only: the unread badge in the chat list, a brief toast and the list under the bell icon. The Browser Notification API is not used in 1.0.

## Safety

- Proactive messages never pressure the user, and never induce guilt for absence ([R-SAF-05](requirements.md#20-safety-requirements-added-by-design)).
- Every proactive message can be turned off from the conversation menu with one action, labeled **Non scrivermi per primo**.
- The profile of each character shows clearly that messages are simulated.

## Tests

See [Testing](testing.md#proactive-messages). Tests use a fake clock and cover quiet hours, caps, unanswered limit, each missed policy, the absence of backdated messages, and the default-off state.
