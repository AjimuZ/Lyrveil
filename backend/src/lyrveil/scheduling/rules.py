"""The pure rules of proactive messages: stored settings, the limits that hold a message back and the stable slots of
`contextual_initiative` (docs/scheduler.md, ADR 0020).

Nothing here reads a clock or a database: every function takes the time and the counted activity as arguments, so the
limits are tested with a fake clock.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# docs/scheduler.md (Evaluation): a user active within this long ago holds a message back, and the event moves later.
RECENT_ACTIVITY = timedelta(minutes=2)
POSTPONE = timedelta(minutes=10)
# docs/scheduler.md (Missed events): an event older than this when found was missed. Slots and follow-ups older than
# it are not created, because the backend was not running when they fell due.
MISSED_AFTER = timedelta(minutes=10)
# docs/scheduler.md (Per-conversation settings) and docs/configuration.md (Proactive).
DEFAULT_MAX_PER_DAY_GLOBAL = 6
MIN_MAX_PER_DAY_GLOBAL = 1
MAX_MAX_PER_DAY_GLOBAL = 24

_CLOCK_TEXT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class EventKind(StrEnum):
    SCHEDULED_MESSAGE = "scheduled_message"
    INACTIVITY_CHECK = "inactivity_check"
    FOLLOW_UP = "follow_up"
    CONTEXTUAL_INITIATIVE = "contextual_initiative"


class MissedPolicy(StrEnum):
    SKIP = "skip"
    DELIVER_ONCE = "deliver_once"
    NEXT_WINDOW = "next_window"


class Limit(StrEnum):
    """The limit that holds a message back, in the order the rules check them."""

    UNANSWERED = "unanswered"
    QUIET_HOURS = "quiet_hours"
    INTERVAL = "interval"
    DAILY_CAP = "daily_cap"
    GLOBAL_DAILY_CAP = "global_daily_cap"


@dataclass(frozen=True)
class Window:
    """A local-time span in which a message may be sent: from `start` included to `end` excluded. A span with
    `start` after `end` crosses midnight. Always holds: `start != end`."""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        if self.start < self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end

    @property
    def minutes(self) -> int:
        """The length of the span in minutes."""
        start = self.start.hour * 60 + self.start.minute
        end = self.end.hour * 60 + self.end.minute
        return (end - start) % (24 * 60)


DEFAULT_WINDOWS = (Window(time(8, 30), time(22, 30)),)


class _WindowDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    start: str = Field(alias="from")
    end: str = Field(alias="to")

    @field_validator("start", "end")
    @classmethod
    def _clock(cls, value: str) -> str:
        if not _CLOCK_TEXT.match(value):
            raise ValueError("a window time is HH:MM")
        return value


class ProactiveSettingsDocument(BaseModel):
    """`conversations.proactive_json` (docs/scheduler.md, Per-conversation settings). A missing field holds its
    default; a value out of its range is an error."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    min_interval_minutes: Annotated[int, Field(ge=60, le=10080)] = 360
    max_per_day: Annotated[int, Field(ge=1, le=6)] = 2
    max_unanswered: Annotated[int, Field(ge=1, le=5)] = 2
    windows: Annotated[list[_WindowDocument], Field(min_length=1, max_length=4)] = Field(
        default_factory=lambda: [_WindowDocument(**{"from": "08:30", "to": "22:30"})]
    )
    inactivity_hours: Annotated[int, Field(ge=1, le=720)] = 24
    kinds: Annotated[list[EventKind], Field(min_length=1)] = Field(default_factory=lambda: list(EventKind))
    missed_policy: MissedPolicy = MissedPolicy.SKIP


@dataclass(frozen=True)
class ProactiveSettings:
    """The settings of one conversation, validated. The default has `enabled` false."""

    enabled: bool = False
    min_interval_minutes: int = 360
    max_per_day: int = 2
    max_unanswered: int = 2
    windows: tuple[Window, ...] = DEFAULT_WINDOWS
    inactivity_hours: int = 24
    kinds: frozenset[EventKind] = frozenset(EventKind)
    missed_policy: MissedPolicy = MissedPolicy.SKIP


def _clock(text: str) -> time:
    return time(int(text[:2]), int(text[3:]))


def to_settings(document: ProactiveSettingsDocument) -> ProactiveSettings:
    """Returns the settings of a validated document. Raises ValueError for a window whose two times are equal."""
    windows = tuple(Window(_clock(window.start), _clock(window.end)) for window in document.windows)
    if any(window.start == window.end for window in windows):
        raise ValueError("a window needs two different times")
    return ProactiveSettings(
        enabled=document.enabled,
        min_interval_minutes=document.min_interval_minutes,
        max_per_day=document.max_per_day,
        max_unanswered=document.max_unanswered,
        windows=windows,
        inactivity_hours=document.inactivity_hours,
        kinds=frozenset(document.kinds),
        missed_policy=document.missed_policy,
    )


def parse_settings(stored: str | None) -> ProactiveSettings:
    """Returns the settings in the stored `proactive_json`. NULL, text that is not valid, or a value out of range gives
    the defaults, which are off: a damaged row never turns proactive messages on."""
    if stored is None:
        return ProactiveSettings()
    try:
        return to_settings(ProactiveSettingsDocument.model_validate(json.loads(stored)))
    except (ValueError, ValidationError):
        return ProactiveSettings()


def parse_global_cap(stored: object) -> int:
    """Returns `proactive.max_per_day_global` from the decoded stored value; anything else, or a value out of range,
    gives DEFAULT_MAX_PER_DAY_GLOBAL."""
    if isinstance(stored, bool) or not isinstance(stored, int):
        return DEFAULT_MAX_PER_DAY_GLOBAL
    if not MIN_MAX_PER_DAY_GLOBAL <= stored <= MAX_MAX_PER_DAY_GLOBAL:
        return DEFAULT_MAX_PER_DAY_GLOBAL
    return stored


@dataclass(frozen=True)
class Activity:
    """What was already sent, counted by the caller. A message counts from the moment its job starts.

    `last_sent_at` is the newest one in the conversation. `sent_today` and `sent_today_global` count those of the local
    day of the evaluation. `unanswered` counts the conversation's messages after the user's last message.
    """

    last_sent_at: datetime | None = None
    sent_today: int = 0
    sent_today_global: int = 0
    unanswered: int = 0


def local_day_bounds(now: datetime, zone: tzinfo) -> tuple[datetime, datetime]:
    """Returns the start (included) and end (excluded) of the local day of `now`, as aware UTC datetimes."""
    day = now.astimezone(zone).date()
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def in_allowed_window(settings: ProactiveSettings, now: datetime, zone: tzinfo) -> bool:
    """Tells whether the local time of `now` is inside one of the windows, so outside quiet hours."""
    local = now.astimezone(zone).time()
    return any(window.contains(local) for window in settings.windows)


def next_window_start(settings: ProactiveSettings, now: datetime, zone: tzinfo) -> datetime:
    """Returns when an event that policy `next_window` moves may next be sent: `now` itself when the local time is
    already inside a window, otherwise the earliest start of a window after `now`, as an aware UTC datetime.

    Always holds: the result is not before `now` and is at most a day after it, because every window repeats daily.
    """
    if in_allowed_window(settings, now, zone):
        return now
    local = now.astimezone(zone)
    starts = []
    for window in settings.windows:
        candidate = datetime.combine(local.date(), window.start, zone)
        if candidate <= now:
            candidate = datetime.combine(local.date() + timedelta(days=1), window.start, zone)
        starts.append(candidate.astimezone(UTC))
    return min(starts)


def blocking_limit(
    settings: ProactiveSettings, activity: Activity, now: datetime, zone: tzinfo, global_cap: int
) -> Limit | None:
    """Returns the first limit that holds a message back at `now`, or None when it may be sent.

    Checked in this order: the unanswered limit, quiet hours, the minimum interval, the conversation's daily cap, the
    global daily cap. A message exactly `min_interval_minutes` after the last one is allowed.
    """
    if activity.unanswered >= settings.max_unanswered:
        return Limit.UNANSWERED
    if not in_allowed_window(settings, now, zone):
        return Limit.QUIET_HOURS
    if activity.last_sent_at is not None and now - activity.last_sent_at < timedelta(
        minutes=settings.min_interval_minutes
    ):
        return Limit.INTERVAL
    if activity.sent_today >= settings.max_per_day:
        return Limit.DAILY_CAP
    if activity.sent_today_global >= global_cap:
        return Limit.GLOBAL_DAILY_CAP
    return None


def user_recently_active(last_user_message_at: datetime | None, now: datetime) -> bool:
    """Tells whether the user wrote within RECENT_ACTIVITY before `now`: then the event moves later by POSTPONE."""
    return last_user_message_at is not None and timedelta(0) <= now - last_user_message_at < RECENT_ACTIVITY


# docs/scheduler.md (Per-conversation settings): how many times a week `contextual_initiative` is evaluated.
WEEKLY_INITIATIVES = {"raramente": 1, "a_volte": 2, "spesso": 4}


def _number(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:8], "big")


def week_start(now: datetime, zone: tzinfo) -> date:
    """Returns the Monday of the local week of `now`."""
    day = now.astimezone(zone).date()
    return day - timedelta(days=day.weekday())


def initiative_slots(
    conversation_id: str, frequency: str, monday: date, window: Window, zone: tzinfo
) -> tuple[datetime, ...]:
    """Returns the instants of the week starting on `monday` at which `contextual_initiative` is evaluated, oldest
    first, as aware UTC datetimes; an unknown `frequency` gives none.

    The days and minutes come from a hash of the conversation and the week, never from a random generator, so they are
    stable across restarts and tests. Every slot is inside `window`, on its own day of the week.
    """
    count = WEEKLY_INITIATIVES.get(frequency, 0)
    week = monday.isoformat()
    days = sorted(range(7), key=lambda day: _number(conversation_id, week, "day", str(day)))[:count]
    span = max(window.minutes, 1)
    slots = []
    for day in days:
        offset = _number(conversation_id, week, "minute", str(day)) % span
        local = datetime.combine(monday + timedelta(days=day), window.start, zone) + timedelta(minutes=offset)
        slots.append(local.astimezone(UTC))
    return tuple(sorted(slots))
