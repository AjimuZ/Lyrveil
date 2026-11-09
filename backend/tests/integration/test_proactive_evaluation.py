"""T-SCH-01 and T-SCH-06 (queue side): SchedulerService on a migrated database with a fake clock.

Checks that nothing is created while the global switch or the conversation's own switch is off, the events each trigger
creates once, that a due event becomes a `proactive_message` job under the limits, the postponement after recent user
activity, that a job needs only the text mode, and that deleting a conversation cancels its pending events.
"""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Connection, Engine, text

from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.jobs.policy import JobClass, JobKind, RequiredMode, policy_for
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import settings as settings_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.scheduling.service import SchedulerService
from lyrveil.services.conversations import ConversationService

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
    return engine


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def service(engine: Engine, clock: FakeClock) -> SchedulerService:
    return SchedulerService(engine, ROME, queue=JobQueue(clock=clock), clock=clock)


def switch(engine: Engine, key: str, value: object) -> None:
    write(engine, lambda connection: settings_repository.set_value(connection, key, value, to_utc_text(NOW)))


def add_chat(
    engine: Engine, *, proactive_json: str | None, silent_for: timedelta = timedelta(days=3), frequency: str = "mai"
) -> str:
    """Inserts a character and a conversation whose last message was `silent_for` before NOW; returns the id."""
    character_id, conversation_id = new_id(), new_id()
    last = to_utc_text(NOW - silent_for)

    def work(connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, communication_json, created_at, updated_at) "
                "VALUES (:id, 'Alba', 30, :communication, :last, :last)"
            ),
            {"id": character_id, "communication": json.dumps({"initiative_frequency": frequency}), "last": last},
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


def add_user_message(engine: Engine, conversation_id: str, at: datetime) -> None:
    def work(connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO messages (id, conversation_id, seq, author, kind, status, content, created_at) "
                "VALUES (:id, :conversation_id, 1, 'user', 'text', 'complete', 'ciao', :at)"
            ),
            {"id": new_id(), "conversation_id": conversation_id, "at": to_utc_text(at)},
        )

    write(engine, work)


def events(engine: Engine) -> list[tuple[str, str, str]]:
    rows = read(
        engine,
        lambda connection: connection.execute(
            text("SELECT kind, status, due_at FROM scheduled_events ORDER BY created_at, id")
        ).all(),
    )
    return [(row.kind, row.status, row.due_at) for row in rows]


def jobs(engine: Engine) -> list[tuple[str, str, str, str]]:
    rows = read(
        engine,
        lambda connection: connection.execute(
            text("SELECT kind, status, stage, required_mode FROM generation_jobs ORDER BY created_at, id")
        ).all(),
    )
    return [tuple(row) for row in rows]


def test_nothing_is_evaluated_while_the_global_switch_is_off(migrated: Engine, clock: FakeClock) -> None:
    add_chat(migrated, proactive_json=ENABLED)

    assert service(migrated, clock).evaluate().events_created == 0
    assert events(migrated) == []
    assert jobs(migrated) == []

    switch(migrated, settings_repository.PROACTIVE_ENABLED, False)
    assert service(migrated, clock).evaluate().events_created == 0
    assert events(migrated) == []


@pytest.mark.parametrize("stored", [None, "{}", json.dumps({"enabled": False}), json.dumps("garbage")])
def test_a_conversation_is_off_by_default_even_when_the_global_switch_is_on(
    migrated: Engine, clock: FakeClock, stored: str | None
) -> None:
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    add_chat(migrated, proactive_json=stored)

    result = service(migrated, clock).evaluate()

    assert (result.events_created, result.jobs_queued) == (0, 0)
    assert events(migrated) == []
    assert jobs(migrated) == []


def test_a_new_conversation_row_has_proactive_messages_off(migrated: Engine, clock: FakeClock) -> None:
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    character_id = new_id()
    write(
        migrated,
        lambda connection: connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, created_at, updated_at) "
                "VALUES (:id, 'Alba', 30, :now, :now)"
            ),
            {"id": character_id, "now": to_utc_text(NOW - timedelta(days=9))},
        ),
    )
    created = ConversationService(migrated, clock=clock).create(character_id)

    assert service(migrated, clock).evaluate().events_created == 0
    assert created is not None
    assert events(migrated) == []


def test_a_long_silence_creates_one_inactivity_event_and_one_text_job(migrated: Engine, clock: FakeClock) -> None:
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    add_chat(migrated, proactive_json=ENABLED)

    first = service(migrated, clock).evaluate()
    second = service(migrated, clock).evaluate()

    assert (first.events_created, first.jobs_queued) == (1, 1)
    assert (second.events_created, second.jobs_queued) == (0, 0)
    assert [(kind, status) for kind, status, _ in events(migrated)] == [("inactivity_check", "running")]
    assert jobs(migrated) == [("proactive_message", "queued", "decide", "llm")]


def test_the_scheduler_only_queues_a_job_that_needs_the_text_mode(migrated: Engine, clock: FakeClock) -> None:
    """T-SCH-06: the job it queues is soft and needs `llm` in every stage, so no mode change can come from it."""
    policy = policy_for(JobKind.PROACTIVE_MESSAGE)
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    add_chat(migrated, proactive_json=ENABLED)

    service(migrated, clock).evaluate()

    assert policy.job_class is JobClass.SOFT
    assert set(policy.stages.values()) == {RequiredMode.LLM}
    assert [row[3] for row in jobs(migrated)] == ["llm"]


def test_a_silence_shorter_than_the_threshold_creates_no_event(migrated: Engine, clock: FakeClock) -> None:
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    add_chat(migrated, proactive_json=ENABLED, silent_for=timedelta(hours=23))

    assert service(migrated, clock).evaluate().events_created == 0


def test_quiet_hours_hold_the_job_back_and_the_event_waits(migrated: Engine, clock: FakeClock) -> None:
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    add_chat(migrated, proactive_json=ENABLED)
    clock.now = datetime(2026, 11, 9, 23, 0, tzinfo=UTC)  # 00:00 local

    result = service(migrated, clock).evaluate()

    assert (result.events_created, result.jobs_queued) == (1, 0)
    assert [(kind, status) for kind, status, _ in events(migrated)] == [("inactivity_check", "pending")]
    assert jobs(migrated) == []

    clock.now = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)  # 09:00 local: the window is open
    result = service(migrated, clock).evaluate()

    assert (result.events_created, result.jobs_queued) == (0, 1)


def test_the_unanswered_limit_keeps_a_conversation_silent_until_the_user_writes(
    migrated: Engine, clock: FakeClock
) -> None:
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    conversation = add_chat(migrated, proactive_json=json.dumps({"enabled": True, "max_unanswered": 1}))
    sent = to_utc_text(NOW - timedelta(days=1))
    write(
        migrated,
        lambda connection: connection.execute(
            text(
                "INSERT INTO scheduled_events (id, conversation_id, character_id, kind, status, due_at, created_by, "
                "created_at, updated_at) SELECT :id, c.id, c.character_id, 'follow_up', 'delivered', :sent, "
                "'scheduler', :sent, :sent FROM conversations AS c WHERE c.id = :conversation"
            ),
            {"id": new_id(), "sent": sent, "conversation": conversation},
        ),
    )

    held = service(migrated, clock).evaluate()
    assert (held.events_created, held.jobs_queued) == (1, 0)

    add_user_message(migrated, conversation, NOW - timedelta(hours=1))
    clock.now = NOW + timedelta(hours=26)  # 13:00 local the next day: only the answer changed the unanswered count
    freed = service(migrated, clock).evaluate()
    assert freed.jobs_queued == 1


def test_recent_user_activity_postpones_the_event_by_ten_minutes(migrated: Engine, clock: FakeClock) -> None:
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    conversation = add_chat(migrated, proactive_json=json.dumps({"enabled": True, "kinds": ["follow_up"]}))
    due = NOW - timedelta(minutes=5)
    memory = new_id()

    def work(connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO memories (id, character_id, scope, conversation_id, kind, content, provenance, "
                "durability, status, importance, confidence, locked, expires_at, fingerprint, language, created_at, "
                "updated_at) SELECT :id, c.character_id, 'conversation', c.id, 'open_thread', 'esame di giovedi', "
                "'stated', 'lasting', 'active', 3, 0.9, 0, :due, 'fp', 'it', :due, :due FROM conversations AS c "
                "WHERE c.id = :conversation"
            ),
            {"id": memory, "conversation": conversation, "due": to_utc_text(due)},
        )

    write(migrated, work)
    add_user_message(migrated, conversation, NOW - timedelta(seconds=30))

    result = service(migrated, clock).evaluate()

    assert (result.events_created, result.jobs_queued) == (1, 0)
    assert events(migrated) == [("follow_up", "pending", to_utc_text(NOW + timedelta(minutes=10)))]
    assert jobs(migrated) == []


def test_a_follow_up_is_created_once_per_open_thread(migrated: Engine, clock: FakeClock) -> None:
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    conversation = add_chat(
        migrated, proactive_json=json.dumps({"enabled": True, "kinds": ["follow_up"]}), silent_for=timedelta(hours=1)
    )

    def work(connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO memories (id, character_id, scope, conversation_id, kind, content, provenance, "
                "durability, status, importance, confidence, locked, expires_at, fingerprint, language, created_at, "
                "updated_at) SELECT :id, c.character_id, 'conversation', c.id, 'open_thread', 'esame di giovedi', "
                "'stated', 'lasting', 'active', 3, 0.9, 0, :due, 'fp', 'it', :due, :due FROM conversations AS c "
                "WHERE c.id = :conversation"
            ),
            {"id": new_id(), "conversation": conversation, "due": to_utc_text(NOW - timedelta(hours=2))},
        )

    write(migrated, work)

    first = service(migrated, clock).evaluate()
    second = service(migrated, clock).evaluate()

    assert (first.events_created, first.jobs_queued) == (1, 1)
    assert second.events_created == 0
    assert [(kind, status) for kind, status, _ in events(migrated)] == [("follow_up", "running")]


def test_deleting_a_conversation_cancels_its_pending_events(migrated: Engine, clock: FakeClock) -> None:
    switch(migrated, settings_repository.PROACTIVE_ENABLED, True)
    conversation = add_chat(migrated, proactive_json=ENABLED)
    clock.now = datetime(2026, 11, 9, 23, 0, tzinfo=UTC)  # quiet hours: the event stays pending
    service(migrated, clock).evaluate()

    ConversationService(migrated, clock=clock).delete(conversation)

    assert [(kind, status) for kind, status, _ in events(migrated)] == [("inactivity_check", "cancelled")]
