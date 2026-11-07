"""InvalidationService: what an edit, a deletion or a variant switch of a message does to the data derived from it
(docs/memory-engine.md, Invalidation after message changes).

MessageService calls it in the transaction of the message change, so the change and its invalidation are committed
together or not at all; only the model work waits for a job. For the changed message: memories that cited the variant
it no longer shows lose that source (a locked one is only flagged `needs_review`), or become `invalidated` when no
source is left; episodes the same; the state rows citing it are invalidated and the state replayed; the summary
checkpoints covering it are invalidated and their level-0 range is queued to be summarized again; and a
`memory_reevaluation` job is queued for the messages around it, which can bring back what still holds.

When a memory in use is invalidated, what it had changed is repaired where the rules allow: the memory it superseded
becomes active again, and the last member left in its conflict group leaves the group, unless that would give a
single-valued subject two values in use or a fingerprint twice.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

from sqlalchemy import Connection

from lyrveil.domain.conversations import Conversation
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.domain.messages import PENDING, Message
from lyrveil.jobs.queue import JobQueue
from lyrveil.logs import get_logger
from lyrveil.memory import consistency
from lyrveil.memory.consistency import KnownMemory
from lyrveil.persistence import conversations as conversation_repository
from lyrveil.persistence import invalidation as repository
from lyrveil.persistence import memory_extraction as memory_repository
from lyrveil.persistence import summaries as summary_repository
from lyrveil.services.character_state import CharacterStateManager
from lyrveil.services.memory_extraction import queue_reevaluation
from lyrveil.services.summaries import queue_resummarize

_IN_USE = frozenset({"active", "uncertain"})

logger = get_logger(__name__)


@dataclass(frozen=True)
class Invalidation:
    """What one message change did to its derived data. Memories: `flagged` (locked, now `needs_review`), `weakened`
    (a source dropped, confidence lowered), `invalidated`, and `restored` (superseded or conflict members that hold
    again). Episodes: `weakened` and `invalidated`. Then the state rows and summary checkpoints invalidated, and the
    jobs queued (summaries and re-evaluation)."""

    memories_flagged: int = 0
    memories_weakened: int = 0
    memories_invalidated: int = 0
    memories_restored: int = 0
    episodes_weakened: int = 0
    episodes_invalidated: int = 0
    state_rows: int = 0
    summaries: int = 0
    jobs_queued: int = 0


class InvalidationService:
    """Invalidates what a changed message supported, in the caller's write transaction, and queues the work that
    rebuilds it on `queue`. `zone` is the deployment time zone the state replay needs (CharacterStateManager)."""

    def __init__(self, queue: JobQueue, zone: tzinfo = UTC) -> None:
        self._queue = queue
        self._state = CharacterStateManager(zone)

    def message_changed(self, connection: Connection, message: Message, now: datetime) -> Invalidation:
        """Applies steps 1 to 6 of the invalidation for `message`, read after its change, and returns what it did.

        Requires the caller's write transaction, holding the change already: the message deleted, or showing its new
        active variant. A source holds while its message is visible and shows the variant the source read, so a
        source of the variant now shown again is not touched. A message still being produced (a regeneration) queues
        no re-evaluation: its reply queues one when it is stored (queue_reevaluation). A full queue skips the job,
        logged, and invalidates all the same. Never commits.
        """
        conversation = conversation_repository.get(connection, message.conversation_id)
        assert conversation is not None, "a message's conversation cannot be removed while the message exists"
        stamp = to_utc_text(now)
        flagged = weakened = invalidated = 0
        lost: list[str] = []
        for memory in repository.affected_memories(connection, message.id):
            change = consistency.after_source_change(
                locked=memory.locked,
                valid_sources_left=memory.valid_sources,
                provenance=memory.provenance,
                confidence=memory.confidence,
            )
            if change.action == "review":
                repository.flag_for_review(connection, memory.id)
                flagged += 1
            elif change.action == "weaken":
                repository.weaken_memory(
                    connection, memory.id, change.confidence, reason=consistency.SOURCE_CHANGED, now=stamp
                )
                weakened += 1
            else:
                repository.invalidate_memory(connection, memory.id, reason=consistency.INVALIDATED, now=stamp)
                invalidated += 1
                if memory.status in _IN_USE:
                    lost.append(memory.id)
        restored = self._repair(connection, conversation, lost, stamp)
        episodes_weakened = episodes_invalidated = 0
        for episode in repository.affected_episodes(connection, message.id):
            if episode.locked:
                continue
            if episode.valid_sources > 0:
                memory_repository.drop_stale_episode_sources(connection, episode.id)
                episodes_weakened += 1
            else:
                repository.invalidate_episode(connection, episode.id, stamp)
                episodes_invalidated += 1
        state_rows = self._state.invalidate(connection, conversation.id, [message.id], now)
        if not state_rows and episodes_invalidated and self._state.current(connection, conversation.id) is not None:
            # The state row lists the newest episodes, which must not name an invalidated one.
            self._state.refresh(connection, conversation.id, now)
        summaries = summary_repository.invalidate_covering(connection, conversation.id, message.seq)
        jobs = 0
        for first, last in summary_repository.invalidated_ranges(connection, conversation.id, message.seq):
            jobs += queue_resummarize(connection, self._queue, conversation.id, first, last) is not None
        pending = message.deleted_at is None and message.status in PENDING
        if not pending:
            jobs += queue_reevaluation(connection, self._queue, conversation.id, message.seq) is not None
        result = Invalidation(
            memories_flagged=flagged,
            memories_weakened=weakened,
            memories_invalidated=invalidated,
            memories_restored=restored,
            episodes_weakened=episodes_weakened,
            episodes_invalidated=episodes_invalidated,
            state_rows=state_rows,
            summaries=summaries,
            jobs_queued=jobs,
        )
        logger.info(
            "derived_data_invalidated",
            conversation_id=conversation.id,
            memory_count=flagged + weakened + invalidated,
            episode_count=episodes_weakened + episodes_invalidated,
            state_count=state_rows,
            summary_count=summaries,
            job_count=jobs,
        )
        return result

    def _repair(self, connection: Connection, conversation: Conversation, lost: Sequence[str], now: str) -> int:
        """Repairs what the memories `lost` (invalidated while in use) had changed and returns how many memories hold
        again. The last member in use of each of their conflict groups leaves it first, then the memories they had
        superseded come back, so a later fact keeps its place before an older one returns. A locked memory is never
        changed, and nothing comes back over a fingerprint or a single-valued subject already in use."""
        restored = 0
        groups = {
            found.conflict_group_id
            for found in (memory_repository.known_memory(connection, memory_id) for memory_id in lost)
            if found is not None and found.conflict_group_id is not None
        }
        for group_id in sorted(groups):
            members = repository.group_members_in_use(connection, conversation.id, group_id)
            if len(members) == 1 and not members[0].locked and self._subject_free(connection, conversation, members[0]):
                repository.leave_conflict(connection, members[0].id, reason=consistency.CONFLICT_CLEARED, now=now)
                restored += 1
        for memory_id in lost:
            for older in repository.superseded_by(connection, memory_id):
                if older.locked or older.kind == consistency.IDENTITY_CANDIDATE:
                    continue
                taken = repository.fingerprint_taken(
                    connection, conversation.character_id, conversation.id, older.fingerprint
                )
                if taken or not self._subject_free(connection, conversation, older):
                    continue
                repository.restore_memory(connection, older.id, reason=consistency.REPLACEMENT_INVALIDATED, now=now)
                restored += 1
        return restored

    @staticmethod
    def _subject_free(connection: Connection, conversation: Conversation, memory: KnownMemory) -> bool:
        """Tells whether `memory` may be in use without breaking guard 3: true unless it is a lasting fact with a
        single-valued subject and a value, and another lasting memory in use holds a different value of it."""
        subject = memory.subject_key
        single = subject is not None and subject in consistency.SINGLE_VALUED_SUBJECTS
        if memory.durability != "lasting" or subject is None or not single or memory.value_text is None:
            return True
        language = conversation.effective_language
        values = repository.subject_values_in_use(connection, conversation.id, subject, memory.id)
        return not any(consistency.values_differ(value, memory.value_text, language) for value in values)
