# Proactive messages and time

Status: accepted (2026-11-01). Implementation: planned, phase P07.

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

**Stage `generate`.** The normal `ContextBuilder` runs with this extra instruction:

```jinja
Stai scrivendo tu per primo a {{ user.display_name }}: non stai rispondendo a un messaggio.
Motivo: {{ reason }}
Scrivi un messaggio breve, naturale e coerente con quello che sai.
Non rimproverare {{ user.display_name }} per il silenzio e non farlo sentire in colpa.
Non inventare eventi accaduti mentre non vi sentivate che contraddicano la tua storia.
```

**Checks.** The usual validators run. A message with similarity of at least 0.8 to one of the last 20 proactive messages is discarded and regenerated once. A second hit marks the event `skipped`.

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
