"""T-SCH-02 and the settings part of T-SCH-01: the pure rules of lyrveil/scheduling/rules.py, with fixed instants in
place of a clock. Covers quiet hours across midnight and the zone, the interval, both daily caps, the unanswered limit,
the order the limits are checked in, the 2-minute activity postponement, damaged settings, and the stable slots of
`contextual_initiative`."""

import ast
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lyrveil.scheduling import rules
from lyrveil.scheduling.rules import (
    DEFAULT_MAX_PER_DAY_GLOBAL,
    Activity,
    EventKind,
    Limit,
    MissedPolicy,
    ProactiveSettings,
    Window,
    blocking_limit,
    in_allowed_window,
    initiative_slots,
    local_day_bounds,
    parse_global_cap,
    parse_settings,
    user_recently_active,
    week_start,
)

ROME = ZoneInfo("Europe/Rome")
# 11:00 in Rome (CET, UTC+1): inside the default window 08:30 to 22:30.
NOON = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
ON = ProactiveSettings(enabled=True)


def test_proactive_messages_are_off_by_default() -> None:
    assert ProactiveSettings().enabled is False
    assert parse_settings(None).enabled is False
    assert parse_settings("{}").enabled is False
    assert parse_settings(json.dumps({"enabled": True})).enabled is True


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        json.dumps({"enabled": True, "max_per_day": 99}),
        json.dumps({"enabled": True, "min_interval_minutes": 5}),
        json.dumps({"enabled": True, "windows": [{"from": "25:00", "to": "08:00"}]}),
        json.dumps({"enabled": True, "windows": [{"from": "09:00", "to": "09:00"}]}),
        json.dumps({"enabled": True, "kinds": []}),
        json.dumps({"enabled": True, "missed_policy": "always"}),
    ],
)
def test_damaged_settings_give_the_defaults_so_they_never_turn_the_feature_on(stored: str) -> None:
    assert parse_settings(stored) == ProactiveSettings()


def test_stored_settings_override_the_defaults() -> None:
    stored = json.dumps(
        {
            "enabled": True,
            "min_interval_minutes": 120,
            "max_per_day": 3,
            "max_unanswered": 1,
            "windows": [{"from": "20:00", "to": "02:00"}],
            "inactivity_hours": 48,
            "kinds": ["follow_up"],
            "missed_policy": "next_window",
        }
    )

    assert parse_settings(stored) == ProactiveSettings(
        enabled=True,
        min_interval_minutes=120,
        max_per_day=3,
        max_unanswered=1,
        windows=(Window(time(20, 0), time(2, 0)),),
        inactivity_hours=48,
        kinds=frozenset({EventKind.FOLLOW_UP}),
        missed_policy=MissedPolicy.NEXT_WINDOW,
    )


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(None, 6), (3, 3), (24, 24), (0, 6), (25, 6), (True, 6), ("4", 6), (2.5, 6)],
)
def test_the_global_cap_falls_back_to_its_default_out_of_range(stored: object, expected: int) -> None:
    assert DEFAULT_MAX_PER_DAY_GLOBAL == 6
    assert parse_global_cap(stored) == expected


def test_quiet_hours_follow_the_local_time_of_the_zone() -> None:
    # 07:00 UTC is 08:00 in Rome: before 08:30. 07:30 UTC is 08:30: the window opens, its start included.
    assert blocking_limit(ON, Activity(), datetime(2026, 11, 9, 7, 0, tzinfo=UTC), ROME, 6) is Limit.QUIET_HOURS
    assert blocking_limit(ON, Activity(), datetime(2026, 11, 9, 7, 30, tzinfo=UTC), ROME, 6) is None
    # 21:29 UTC is 22:29 local, still inside; 21:30 UTC is 22:30, whose end is excluded.
    assert blocking_limit(ON, Activity(), datetime(2026, 11, 9, 21, 29, tzinfo=UTC), ROME, 6) is None
    assert blocking_limit(ON, Activity(), datetime(2026, 11, 9, 21, 30, tzinfo=UTC), ROME, 6) is Limit.QUIET_HOURS


def test_a_window_that_crosses_midnight_holds_both_sides() -> None:
    night = ProactiveSettings(enabled=True, windows=(Window(time(22, 0), time(2, 0)),))

    assert in_allowed_window(night, datetime(2026, 11, 9, 22, 30, tzinfo=UTC), ROME)  # 23:30 local
    assert in_allowed_window(night, datetime(2026, 11, 9, 0, 30, tzinfo=UTC), ROME)  # 01:30 local
    assert not in_allowed_window(night, datetime(2026, 11, 9, 12, 0, tzinfo=UTC), ROME)  # 13:00 local


def test_the_local_clock_change_moves_the_window_in_utc() -> None:
    # After the end of summer time (2026-10-25) Rome is UTC+1; before it, UTC+2.
    summer = datetime(2026, 10, 20, 6, 30, tzinfo=UTC)  # 08:30 local
    winter = datetime(2026, 11, 9, 6, 30, tzinfo=UTC)  # 07:30 local

    assert in_allowed_window(ON, summer, ROME)
    assert not in_allowed_window(ON, winter, ROME)


def test_the_interval_holds_a_message_back_until_it_has_passed() -> None:
    last = NOON - timedelta(minutes=360)

    assert blocking_limit(ON, Activity(last_sent_at=last + timedelta(seconds=1)), NOON, ROME, 6) is Limit.INTERVAL
    # Exactly min_interval_minutes after the last message is allowed.
    assert blocking_limit(ON, Activity(last_sent_at=last), NOON, ROME, 6) is None
    assert blocking_limit(ON, Activity(last_sent_at=None), NOON, ROME, 6) is None


def test_the_daily_cap_of_the_conversation_and_the_global_cap_are_separate() -> None:
    assert blocking_limit(ON, Activity(sent_today=1), NOON, ROME, 6) is None
    assert blocking_limit(ON, Activity(sent_today=2), NOON, ROME, 6) is Limit.DAILY_CAP
    assert blocking_limit(ON, Activity(sent_today=1, sent_today_global=5), NOON, ROME, 6) is None
    assert blocking_limit(ON, Activity(sent_today=1, sent_today_global=6), NOON, ROME, 6) is Limit.GLOBAL_DAILY_CAP
    assert blocking_limit(ON, Activity(sent_today=1, sent_today_global=6), NOON, ROME, 7) is None


def test_the_unanswered_limit_keeps_the_conversation_silent_until_the_user_writes() -> None:
    assert blocking_limit(ON, Activity(unanswered=1), NOON, ROME, 6) is None
    assert blocking_limit(ON, Activity(unanswered=2), NOON, ROME, 6) is Limit.UNANSWERED
    assert blocking_limit(ON, Activity(unanswered=9), NOON, ROME, 6) is Limit.UNANSWERED


def test_the_limits_are_reported_in_a_fixed_order() -> None:
    everything = Activity(last_sent_at=NOON - timedelta(minutes=5), sent_today=5, sent_today_global=9, unanswered=4)
    night = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)

    assert blocking_limit(ON, everything, night, ROME, 6) is Limit.UNANSWERED
    assert blocking_limit(ON, Activity(last_sent_at=night, sent_today=5), night, ROME, 6) is Limit.QUIET_HOURS
    assert blocking_limit(ON, Activity(last_sent_at=NOON - timedelta(minutes=5), sent_today=5), NOON, ROME, 6) is (
        Limit.INTERVAL
    )


def test_the_local_day_runs_from_local_midnight_to_the_next() -> None:
    start, end = local_day_bounds(datetime(2026, 11, 9, 23, 30, tzinfo=UTC), ROME)  # 00:30 on the 10th, local

    assert start == datetime(2026, 11, 9, 23, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 10, 23, 0, tzinfo=UTC)


def test_a_user_active_in_the_last_two_minutes_postpones_the_message() -> None:
    assert user_recently_active(NOON - timedelta(seconds=119), NOON)
    assert not user_recently_active(NOON - timedelta(minutes=2), NOON)
    assert not user_recently_active(None, NOON)
    assert not user_recently_active(NOON + timedelta(seconds=1), NOON)


def test_initiative_slots_are_stable_inside_the_window_and_match_the_frequency() -> None:
    monday = week_start(NOON, ROME)
    window = Window(time(8, 30), time(22, 30))

    assert monday.isoformat() == "2026-11-09"
    for frequency, count in (("raramente", 1), ("a_volte", 2), ("spesso", 4)):
        slots = initiative_slots("conversation-1", frequency, monday, window, ROME)
        assert len(slots) == count
        assert slots == initiative_slots("conversation-1", frequency, monday, window, ROME)
        assert len({slot.astimezone(ROME).date() for slot in slots}) == count
        for slot in slots:
            assert window.contains(slot.astimezone(ROME).time())
            assert monday <= slot.astimezone(ROME).date() < monday + timedelta(days=7)
    assert initiative_slots("conversation-1", "mai", monday, window, ROME) == ()


def test_the_scheduling_package_cannot_reach_the_orchestrator_or_a_runtime() -> None:
    """T-SCH-06: with no import of the orchestrator client, the runtime ports or the GPU state, the scheduler has no way
    to ask for a mode change; the only thing it writes is a queued job, which needs the text mode."""
    forbidden = ("lyrveil.orchestration", "lyrveil.ports", "lyrveil.runtimes", "lyrveil.gpu")
    package = Path(rules.__file__).parent
    imported: set[str] = set()
    for source in package.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

    assert not [name for name in imported if name.startswith(forbidden)]


def test_initiative_slots_differ_between_conversations_and_weeks() -> None:
    window = Window(time(8, 30), time(22, 30))
    monday = week_start(NOON, ROME)

    slots = {
        initiative_slots(name, "spesso", monday + timedelta(days=7 * week), window, ROME)
        for name in ("a", "b", "c")
        for week in range(3)
    }

    assert len(slots) > 1
