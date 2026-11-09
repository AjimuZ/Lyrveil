"""T-SCH-03: the missed-event policies of SchedulerService.resolve_missed on a migrated database with a fake clock.

Checks each policy (`skip`, `deliver_once`, `next_window`), that no message is written for a missed event, that one
`missed_events` notification carries the count and nothing else, that events within 10 minutes and those of switched-off
conversations are left alone, and that a `running` event with no live job is ended.
"""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Connection, Engine, text

from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import settings as settings_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.scheduling import store
from lyrveil.scheduling.rules import EventKind, MissedPolicy
from lyrveil.scheduling.service import MissedResolution, SchedulerService

ROME = ZoneInfo("Europe/Rome")
# 11:00 in Rome, inside the default window.
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
ENABLED = json.dumps({"enabled": True})


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    write(
        engine,
        lambda connection: settings_repository.set_value(
            connection, settings_repository.PROACTIVE_ENABLED, True, to_utc_text(NOW)
        ),
    )
    return engine


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def service(engine: Engine, clock: FakeClock) -> SchedulerService:
    return SchedulerService(engine, ROME, queue=JobQueue(clock=clock), clock=clock)


def add_chat(engine: Engine, *, proactive_json: str | None = ENABLED) -> str:
    """Inserts a character and a conversation whose last message was a day ago; returns the conversation id."""
    character_id, conversation_id = new_id(), new_id()
    last = to_utc_text(NOW - timedelta(hours=1))

    def work(connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, communication_json, created_at, updated_at) "
                "VALUES (:id, 'Alba', 30, :communication, :last, :last)"
            ),
            {"id": character_id, "communication": json.dumps({"initiative_frequency": "mai"}), "last": last},
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, character_id, title, language, proactive_json, last_message_at, "
                "created_at, updated_at) VALUES (:id, :character_id, 'Chat', 'it', :proactive, :last, :last, :last)"
            ),
            {"id": conversation_id, "character_id": character_id, "proactive": proactive_json, "last": last},
        )

    write(engine, work)
    return conversation_id


def add_event(engine: Engine, conversation_id: str, ago: timedelta, policy: MissedPolicy) -> str:
    """Inserts a pending `scheduled_message` event due `ago` before NOW with `policy`; returns its id."""

    def work(connection: Connection) -> str:
        character_id = connection.execute(
            text("SELECT character_id FROM conversations WHERE id = :id"), {"id": conversation_id}
        ).scalar_one()
        return store.insert_event(
            connection,
            conversation_id=conversation_id,
            character_id=character_id,
            kind=EventKind.SCHEDULED_MESSAGE,
            due_at=to_utc_text(NOW - ago),
            note="Ricordami l'esame",
            missed_policy=policy,
            created_by="user",
            now=to_utc_text(NOW - timedelta(days=1)),
        )

    return write(engine, work)


def event_states(engine: Engine) -> dict[str, tuple[str, str | None, str]]:
    """Maps each event id to its status, outcome reason and `due_at`."""
    rows = read(
        engine,
        lambda connection: connection.execute(
            text("SELECT id, status, outcome_reason, due_at FROM scheduled_events")
        ).all(),
    )
    return {row.id: (row.status, row.outcome_reason, row.due_at) for row in rows}


def notification_rows(engine: Engine) -> list[tuple[str, str, str, str | None, str | None, str | None]]:
    rows = read(
        engine,
        lambda connection: connection.execute(
            text("SELECT kind, title, body, conversation_id, message_id, read_at FROM notifications")
        ).all(),
    )
    return [tuple(row) for row in rows]


def message_count(engine: Engine) -> int:
    return int(read(engine, lambda connection: connection.execute(text("SELECT count(*) FROM messages")).scalar_one()))


def test_skip_ends_every_overdue_event_missed_and_leaves_a_recent_one(migrated: Engine, clock: FakeClock) -> None:
    conversation = add_chat(migrated)
    old = add_event(migrated, conversation, timedelta(hours=5), MissedPolicy.SKIP)
    older = add_event(migrated, conversation, timedelta(days=2), MissedPolicy.SKIP)
    recent = add_event(migrated, conversation, timedelta(minutes=9), MissedPolicy.SKIP)

    result = service(migrated, clock).resolve_missed()

    assert result == MissedResolution(found=2, missed=2)
    states = event_states(migrated)
    assert states[old][:2] == ("missed", "policy_skip")
    assert states[older][:2] == ("missed", "policy_skip")
    assert states[recent][0] == "pending"
    assert message_count(migrated) == 0


def test_the_notification_has_the_count_and_no_invented_content(migrated: Engine, clock: FakeClock) -> None:
    conversation = add_chat(migrated)
    add_event(migrated, conversation, timedelta(hours=5), MissedPolicy.SKIP)
    add_event(migrated, conversation, timedelta(hours=6), MissedPolicy.DELIVER_ONCE)
    add_event(migrated, conversation, timedelta(hours=7), MissedPolicy.NEXT_WINDOW)

    service(migrated, clock).resolve_missed()

    [(kind, title, body, notification_conversation, message, read_at)] = notification_rows(migrated)
    assert kind == "missed_events"
    assert title == "Messaggi programmati scaduti"
    assert "3 messaggi programmati" in body
    assert "Ricordami" not in body
    assert (notification_conversation, message, read_at) == (None, None, None)

    # The events were handled: a second pass finds nothing and adds no notification.
    again = service(migrated, clock).resolve_missed()
    assert again.found == 0
    assert len(notification_rows(migrated)) == 1


def test_one_missed_event_is_reported_in_the_singular(migrated: Engine, clock: FakeClock) -> None:
    add_event(migrated, add_chat(migrated), timedelta(hours=5), MissedPolicy.SKIP)

    service(migrated, clock).resolve_missed()

    [(_, _, body, *_)] = notification_rows(migrated)
    assert body.startswith("All'avvio 1 messaggio programmato risultava scaduto")


def test_nothing_missed_writes_no_notification(migrated: Engine, clock: FakeClock) -> None:
    add_event(migrated, add_chat(migrated), timedelta(minutes=9), MissedPolicy.SKIP)

    assert service(migrated, clock).resolve_missed() == MissedResolution()
    assert notification_rows(migrated) == []


def test_deliver_once_keeps_only_the_newest_event_of_each_conversation_and_queues_it_with_no_message(
    migrated: Engine, clock: FakeClock
) -> None:
    conversation = add_chat(migrated)
    other = add_chat(migrated)
    oldest = add_event(migrated, conversation, timedelta(hours=9), MissedPolicy.DELIVER_ONCE)
    middle = add_event(migrated, conversation, timedelta(hours=5), MissedPolicy.DELIVER_ONCE)
    newest = add_event(migrated, conversation, timedelta(hours=2), MissedPolicy.DELIVER_ONCE)
    alone = add_event(migrated, other, timedelta(hours=3), MissedPolicy.DELIVER_ONCE)

    result = service(migrated, clock).resolve_missed()

    assert result == MissedResolution(found=4, missed=2, kept=2)
    states = event_states(migrated)
    assert states[oldest][:2] == ("missed", "newer_missed_event")
    assert states[middle][:2] == ("missed", "newer_missed_event")
    assert states[newest][0] == "pending"
    assert states[alone][0] == "pending"
    assert message_count(migrated) == 0

    # The kept event is due now; the evaluation queues it, and only the job can write a message.
    assert states[newest][2] == to_utc_text(NOW)
    evaluation = service(migrated, clock).evaluate()
    assert evaluation.jobs_queued == 2
    assert event_states(migrated)[newest][0] == "running"
    assert message_count(migrated) == 0


def test_deliver_once_waits_for_the_next_allowed_moment(migrated: Engine, clock: FakeClock) -> None:
    clock.now = datetime(2026, 11, 9, 23, 0, tzinfo=UTC)  # 00:00 local, quiet hours
    conversation = add_chat(migrated)
    event = add_event(migrated, conversation, timedelta(hours=2), MissedPolicy.DELIVER_ONCE)

    service(migrated, clock).resolve_missed()
    assert event_states(migrated)[event][0] == "pending"
    assert service(migrated, clock).evaluate().jobs_queued == 0

    clock.now = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)  # 09:00 local
    assert service(migrated, clock).evaluate().jobs_queued == 1


def test_next_window_moves_the_event_to_the_start_of_the_next_window(migrated: Engine, clock: FakeClock) -> None:
    clock.now = datetime(2026, 11, 9, 23, 0, tzinfo=UTC)  # 00:00 local on the 10th, quiet hours
    conversation = add_chat(migrated)
    event = add_event(migrated, conversation, timedelta(hours=4), MissedPolicy.NEXT_WINDOW)

    result = service(migrated, clock).resolve_missed()

    assert result == MissedResolution(found=1, moved=1)
    status, reason, due_at = event_states(migrated)[event]
    assert (status, reason) == ("pending", None)
    assert due_at == to_utc_text(datetime(2026, 11, 10, 7, 30, tzinfo=UTC))  # 08:30 local
    assert service(migrated, clock).evaluate().jobs_queued == 0
    assert message_count(migrated) == 0


def test_next_window_inside_a_window_is_due_now(migrated: Engine, clock: FakeClock) -> None:
    conversation = add_chat(migrated)
    event = add_event(migrated, conversation, timedelta(hours=4), MissedPolicy.NEXT_WINDOW)

    service(migrated, clock).resolve_missed()

    assert event_states(migrated)[event][2] == to_utc_text(NOW)


def test_nothing_is_resolved_while_a_switch_is_off(migrated: Engine, clock: FakeClock) -> None:
    on = add_chat(migrated)
    off = add_chat(migrated, proactive_json=None)
    kept = add_event(migrated, off, timedelta(hours=5), MissedPolicy.SKIP)
    skipped = add_event(migrated, on, timedelta(hours=5), MissedPolicy.SKIP)

    assert service(migrated, clock).resolve_missed() == MissedResolution(found=1, missed=1)
    assert event_states(migrated)[kept][0] == "pending"
    assert event_states(migrated)[skipped][0] == "missed"

    write(
        migrated,
        lambda connection: settings_repository.set_value(
            connection, settings_repository.PROACTIVE_ENABLED, False, to_utc_text(NOW)
        ),
    )
    later = add_event(migrated, on, timedelta(hours=6), MissedPolicy.SKIP)
    assert service(migrated, clock).resolve_missed() == MissedResolution()
    assert event_states(migrated)[later][0] == "pending"
    assert len(notification_rows(migrated)) == 1


def test_a_running_event_with_no_live_job_is_ended_and_one_with_a_queued_job_is_kept(
    migrated: Engine, clock: FakeClock
) -> None:
    conversation = add_chat(migrated)
    orphan = add_event(migrated, conversation, timedelta(minutes=1), MissedPolicy.SKIP)
    live = add_event(migrated, conversation, timedelta(minutes=2), MissedPolicy.SKIP)

    def work(connection: Connection) -> None:
        character_id = connection.execute(
            text("SELECT character_id FROM conversations WHERE id = :id"), {"id": conversation}
        ).scalar_one()
        job = JobQueue(clock=clock).enqueue(
            connection,
            JobKind.PROACTIVE_MESSAGE,
            {"event_id": live, "event_kind": "scheduled_message"},
            conversation_id=conversation,
            character_id=character_id,
        )
        store.start(connection, live, job.id, to_utc_text(NOW))
        connection.execute(
            text("UPDATE scheduled_events SET status = 'running', updated_at = :now WHERE id = :id"),
            {"id": orphan, "now": to_utc_text(NOW)},
        )

    write(migrated, work)

    result = service(migrated, clock).resolve_missed()

    assert result == MissedResolution(interrupted=1)
    states = event_states(migrated)
    assert states[orphan][:2] == ("failed", "interrupted")
    assert states[live][0] == "running"
    assert notification_rows(migrated) == []
