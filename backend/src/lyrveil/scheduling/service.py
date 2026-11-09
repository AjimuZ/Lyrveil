"""SchedulerService: once at startup, applies the missed-event policies; then every minute, creates the due proactive
events and queues a `proactive_message` job for the ones the limits allow (docs/scheduler.md, Missed events and
Evaluation).

It only reads and writes the database. It has no port to the orchestrator and no GPU state, so it cannot ask for a
mode change: the job it queues is `soft` and needs `llm` (lyrveil.jobs.policy), and JobScheduler runs it only in text
mode, only after the user has been idle, and never takes the GPU from an image for it (ADR 0020, ADR 0010).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

import anyio
from anyio import to_thread
from sqlalchemy import Connection, Engine

from lyrveil.domain.identifiers import from_utc_text, to_utc_text
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import JobQueue, QueueFullError
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.persistence import notifications
from lyrveil.persistence import settings as settings_repository
from lyrveil.scheduling import store
from lyrveil.scheduling.rules import (
    MISSED_AFTER,
    POSTPONE,
    EventKind,
    MissedPolicy,
    ProactiveSettings,
    blocking_limit,
    initiative_slots,
    local_day_bounds,
    next_window_start,
    parse_global_cap,
    parse_settings,
    user_recently_active,
    week_start,
)

# docs/scheduler.md: the backend evaluates every 60 seconds.
EVALUATION_INTERVAL_S = 60.0
# An open thread whose `valid_until` passed longer ago than this no longer asks for a follow-up.
FOLLOW_UP_LOOKBACK = timedelta(days=7)

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _missed_body(count: int) -> str:
    """The Italian text of the `missed_events` notification: the count and a statement that nothing was invented.
    It names no conversation, character or topic, because nothing about the missed events is known to have happened."""
    if count == 1:
        found = "1 messaggio programmato risultava scaduto"
    else:
        found = f"{count} messaggi programmati risultavano scaduti"
    return f"All'avvio {found} da più di 10 minuti. Nessun messaggio è stato scritto al posto loro."


def _switched_on(connection: Connection) -> bool:
    return settings_repository.get_flag(connection, settings_repository.PROACTIVE_ENABLED, False)


@dataclass(frozen=True)
class Evaluation:
    """What one pass did: the events it created and the jobs it queued."""

    events_created: int = 0
    jobs_queued: int = 0


@dataclass(frozen=True)
class MissedResolution:
    """What the startup step did. `found` is the overdue events it looked at; the other counts split them by outcome:
    `missed` ended `missed`, `kept` is the `deliver_once` event left pending for delivery, `moved` is the `next_window`
    events whose `due_at` moved. `interrupted` is the `running` events with no live job it ended `failed`."""

    found: int = 0
    missed: int = 0
    kept: int = 0
    moved: int = 0
    interrupted: int = 0


class SchedulerService:
    """Evaluates the proactive rules of `engine` and queues jobs on `queue`.

    `zone` is `LYRVEIL_TZ`: quiet hours, the day of the caps and the slots of `contextual_initiative` follow its local
    time. `clock` returns the aware current time; tests pass a fake one. Never raises from run_forever.
    """

    def __init__(
        self,
        engine: Engine,
        zone: tzinfo,
        *,
        queue: JobQueue | None = None,
        clock: Callable[[], datetime] = _utc_now,
        interval_s: float = EVALUATION_INTERVAL_S,
    ) -> None:
        self._engine = engine
        self._zone = zone
        self._clock = clock
        self._queue = queue if queue is not None else JobQueue(clock=clock)
        self._interval_s = interval_s

    def evaluate(self) -> Evaluation:
        """Runs one pass and returns what it did. Blocks; run it in a worker thread.

        Does nothing, and takes no write lock, while `proactive.enabled` is off, which is the default. Otherwise, in one
        transaction and for each conversation whose own `enabled` is true: creates the events that are due, then
        queues at most one job, for the oldest due event, when no limit holds it back, no other message of the
        conversation is being written and the user was not active in the last 2 minutes (the event then moves 10
        minutes later). A full queue ends the pass; the event stays pending for the next one.
        """

        if not read(self._engine, _switched_on):
            return Evaluation()
        return write(self._engine, self._pass)

    def resolve_missed(self) -> MissedResolution:
        """Applies the missed-event policies once, at startup (docs/scheduler.md, Missed events). Blocks; run it in a
        worker thread.

        Does nothing while `proactive.enabled` is off, and leaves the events of a conversation whose own switch is
        off as they are. For each other conversation, a `pending` event whose `due_at` is more than 10 minutes old is
        missed, and its own `missed_policy` decides: `skip` ends it `missed`; `deliver_once` keeps the newest such
        event of the conversation pending and due now, which the evaluation queues at the next allowed moment, and
        ends the others `missed`; `next_window` moves `due_at` to the next allowed moment (rules.next_window_start). It
        never writes a message, so no message gets a time earlier than its generation. When anything was found it
        writes one `missed_events` notification with the count and no other content. All in one transaction.
        """
        if not read(self._engine, _switched_on):
            return MissedResolution()
        return write(self._engine, self._resolve)

    def _resolve(self, connection: Connection) -> MissedResolution:
        now = self._clock()
        stamp = to_utc_text(now)
        interrupted = store.end_interrupted_events(connection, stamp)
        found = missed = kept = moved = 0
        for conversation in store.enabled_conversations(connection):
            settings = parse_settings(conversation.proactive_json)
            if not settings.enabled:
                continue
            stale = store.stale_pending_events(connection, conversation.id, to_utc_text(now - MISSED_AFTER))
            found += len(stale)
            # The list is oldest first, so the last `deliver_once` event is the newest one.
            deliverable = [event.id for event in stale if event.missed_policy is MissedPolicy.DELIVER_ONCE]
            newest = deliverable[-1] if deliverable else None
            for event in stale:
                if event.missed_policy is MissedPolicy.NEXT_WINDOW:
                    due_at = to_utc_text(next_window_start(settings, now, self._zone))
                    store.postpone(connection, event.id, due_at, stamp)
                    moved += 1
                elif event.id == newest:
                    # Due now, so the evaluation takes it at the next allowed moment and a second step does not
                    # find it overdue again. The message it makes is stamped when it is written, never earlier.
                    store.postpone(connection, event.id, stamp, stamp)
                    kept += 1
                else:
                    superseded = event.missed_policy is MissedPolicy.DELIVER_ONCE
                    reason = "newer_missed_event" if superseded else "policy_skip"
                    store.mark_missed(connection, event.id, reason, stamp)
                    missed += 1
        if found:
            notifications.insert_notification(
                connection,
                kind=notifications.MISSED_EVENTS,
                title="Messaggi programmati scaduti",
                body=_missed_body(found),
                now=stamp,
            )
        return MissedResolution(found, missed, kept, moved, interrupted)

    def _pass(self, connection: Connection) -> Evaluation:
        now = self._clock()
        cap = parse_global_cap(
            settings_repository.get_value(connection, settings_repository.PROACTIVE_MAX_PER_DAY_GLOBAL)
        )
        events_created = jobs_queued = 0
        for conversation in store.enabled_conversations(connection):
            settings = parse_settings(conversation.proactive_json)
            if not settings.enabled:
                continue
            events_created += self._create_events(connection, conversation, settings, now)
            try:
                jobs_queued += self._queue_one(connection, conversation, settings, now, cap)
            except QueueFullError:
                logger.warning("proactive_queue_full")
                break
        return Evaluation(events_created, jobs_queued)

    def _create_events(
        self, connection: Connection, conversation: store.ConversationRow, settings: ProactiveSettings, now: datetime
    ) -> int:
        """Creates the scheduler's events that are due now and have no event yet. Returns how many it made."""
        stamp = to_utc_text(now)
        made = 0

        def create(kind: EventKind, due_at: str, note: str | None) -> None:
            nonlocal made
            store.insert_event(
                connection,
                conversation_id=conversation.id,
                character_id=conversation.character_id,
                kind=kind,
                due_at=due_at,
                note=note,
                missed_policy=settings.missed_policy,
                created_by="scheduler",
                now=stamp,
            )
            made += 1

        if EventKind.INACTIVITY_CHECK in settings.kinds:
            last = conversation.last_message_at or conversation.created_at
            quiet_for = now - from_utc_text(last)
            # One check per silence: an event created since the last message already covers it.
            if quiet_for >= timedelta(hours=settings.inactivity_hours) and not store.has_event(
                connection, conversation.id, EventKind.INACTIVITY_CHECK, created_since=last
            ):
                create(EventKind.INACTIVITY_CHECK, stamp, None)
        if EventKind.FOLLOW_UP in settings.kinds:
            threads = store.expired_open_threads(connection, conversation, to_utc_text(now - FOLLOW_UP_LOOKBACK), stamp)
            for thread in threads:
                if not store.has_event(connection, conversation.id, EventKind.FOLLOW_UP, note=thread):
                    create(EventKind.FOLLOW_UP, stamp, thread)
        if EventKind.CONTEXTUAL_INITIATIVE in settings.kinds and conversation.initiative_frequency is not None:
            window = min(settings.windows, key=lambda candidate: candidate.start)
            slots = initiative_slots(
                conversation.id, conversation.initiative_frequency, week_start(now, self._zone), window, self._zone
            )
            for slot in slots:
                # A slot that fell while the backend was off is not created: nothing is simulated afterwards.
                if not timedelta(0) <= now - slot <= MISSED_AFTER:
                    continue
                due_at = to_utc_text(slot)
                if not store.has_event(connection, conversation.id, EventKind.CONTEXTUAL_INITIATIVE, due_at=due_at):
                    create(EventKind.CONTEXTUAL_INITIATIVE, due_at, None)
        return made

    def _queue_one(
        self,
        connection: Connection,
        conversation: store.ConversationRow,
        settings: ProactiveSettings,
        now: datetime,
        global_cap: int,
    ) -> int:
        """Queues a job for the oldest due event of an enabled kind when the rules allow it. Returns 1 or 0."""
        stamp = to_utc_text(now)
        event = next(
            (event for event in store.due_events(connection, conversation.id, stamp) if event.kind in settings.kinds),
            None,
        )
        if event is None or store.has_running_event(connection, conversation.id):
            return 0
        last_user_at = store.last_user_message_at(connection, conversation.id)
        start, end = local_day_bounds(now, self._zone)
        activity = store.activity(connection, conversation.id, to_utc_text(start), to_utc_text(end), last_user_at)
        limit = blocking_limit(settings, activity, now, self._zone, global_cap)
        if limit is not None:
            logger.info(
                "proactive_held_back", conversation_id=conversation.id, event_kind=event.kind.value, limit=limit.value
            )
            return 0
        if user_recently_active(last_user_at, now):
            store.postpone(connection, event.id, to_utc_text(now + POSTPONE), stamp)
            return 0
        job = self._queue.enqueue(
            connection,
            JobKind.PROACTIVE_MESSAGE,
            {"event_id": event.id, "event_kind": event.kind.value},
            conversation_id=conversation.id,
            character_id=conversation.character_id,
            idempotency_key=f"proactive_message:{event.id}",
        )
        store.start(connection, event.id, job.id, stamp)
        logger.info("proactive_job_queued", conversation_id=conversation.id, event_kind=event.kind.value)
        return 1

    async def run_forever(self) -> None:
        """Applies the missed-event policies once, then evaluates every interval until cancelled. A failed step or
        pass is logged and the next pass runs on time."""
        try:
            resolution = await to_thread.run_sync(self.resolve_missed)
            if resolution.found or resolution.interrupted:
                logger.info(
                    "proactive_missed_resolved",
                    found=resolution.found,
                    missed=resolution.missed,
                    kept=resolution.kept,
                    moved=resolution.moved,
                    interrupted=resolution.interrupted,
                )
        except Exception:
            logger.error("proactive_missed_failed", exc_info=True)
        while True:
            await anyio.sleep(self._interval_s)
            try:
                await to_thread.run_sync(self.evaluate)
            except Exception:
                logger.error("proactive_evaluation_failed", exc_info=True)
