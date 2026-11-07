"""CharacterStateManager: the per-conversation state of docs/consistency-engine.md (Character State Manager), kept as
the fold (lyrveil.consistency.state.replay) of the conversation's applied `character_state_history` rows.

Every method works in the caller's write transaction and never commits, so a run's state rows are written with its
memories, run row and cursor, or not at all. Each time rows are added or invalidated the `relationship_states` row is
written again from a replay of the applied rows read back, so it always equals that replay. The row is created by the
first run that writes a history row; a conversation without one has no state block in its prompt. Every read and write
names one conversation, so a change in one chat never reaches another.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

from sqlalchemy import Connection

from lyrveil.consistency.state import (
    RECENT_EVENTS,
    RELATIONSHIP_CHANGE,
    Proposal,
    RelationshipState,
    StateEvent,
    relationship_proposal,
    replay,
    update_proposals,
)
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.memory_extraction import RelationshipEvent, StateUpdate
from lyrveil.persistence import memory_extraction as episode_repository
from lyrveil.persistence import relationship as repository


@dataclass(frozen=True)
class StateRun:
    """The extraction run whose state changes are applied: its conversation, character and language, the ids of its
    window messages (the sources of the StateUpdate's events), whether an episode of the run allows a `large` change,
    and the time of its commit."""

    run_id: str
    conversation_id: str
    character_id: str
    language: str
    window_message_ids: tuple[str, ...]
    large_allowed: bool
    now: datetime


@dataclass(frozen=True)
class StateApplied:
    """What one run did to the state: the history rows it wrote, the items it dropped (no source, a blank or too long
    text, a close of no open thread, a list over its per-run limit) and those it could not apply yet."""

    rows_added: int
    dropped: int
    deferred: int


class CharacterStateManager:
    """Reads, changes and replays the state of one conversation at a time. `zone` is the deployment time zone, whose
    calendar days bound the daily change (docs/consistency-engine.md, Update rules)."""

    def __init__(self, zone: tzinfo = UTC) -> None:
        self._zone = zone

    def current(self, connection: Connection, conversation_id: str) -> RelationshipState | None:
        """Returns the stored state of the conversation, or None when no run has written one yet."""
        stored = repository.load_state(connection, conversation_id)
        return None if stored is None else stored.state

    def apply_run(
        self,
        connection: Connection,
        run: StateRun,
        events: Sequence[tuple[RelationshipEvent, Sequence[str]]],
        update: StateUpdate,
    ) -> StateApplied:
        """Applies one run's relationship events and state update, and returns what it did.

        `events` pairs each event with the ids of its source messages, already checked to be in the window. Each change
        becomes one `applied` row with its reason and sources, in this order: the events as given, then the text events
        of `update` (lyrveil.consistency.state.update_proposals). A relationship row keeps the requested delta in
        `delta` and the value before and after the limits in `value_before` and `value_after`. When the run writes a
        row, or the conversation already has a state, the state row is written again, so its recent episodes follow
        the run's. Requires `run.window_message_ids` to name at least one message.
        """
        history = repository.applied_events(connection, run.conversation_id)
        before = replay(history, self._zone).state
        proposals: list[Proposal] = []
        dropped = 0
        for event, sources in events:
            proposal = relationship_proposal(event, sources, run.large_allowed)
            if proposal is None:
                dropped += 1
            else:
                proposals.append(proposal)
        planned = update_proposals(before, update, run.window_message_ids, run.language)
        proposals.extend(planned.proposals)
        now = to_utc_text(run.now)
        if proposals:
            self._write(connection, run, history, proposals, now)
        if proposals or repository.load_state(connection, run.conversation_id) is not None:
            self._store(connection, run.conversation_id, now)
        return StateApplied(len(proposals), dropped + planned.dropped, planned.deferred)

    def invalidate(
        self, connection: Connection, conversation_id: str, message_ids: Sequence[str], now: datetime
    ) -> int:
        """Marks `invalidated` the applied rows of the conversation that cite one of `message_ids`, stores the state
        replayed from the rows that remain, and returns how many rows it invalidated; with none it writes nothing.

        The replay gives the state the conversation would have if the invalidated rows had never been written: the
        limits of the remaining rows are computed again without them. Invalidation after a message change (P04.7)
        calls it when a message is edited or deleted or its active variant changes.
        """
        changed = repository.invalidate_citing(connection, conversation_id, message_ids)
        if changed:
            self._store(connection, conversation_id, to_utc_text(now))
        return changed

    def refresh(self, connection: Connection, conversation_id: str, now: datetime) -> RelationshipState:
        """Stores and returns the state replayed from the conversation's applied rows, whatever the row held."""
        return self._store(connection, conversation_id, to_utc_text(now))

    def _write(
        self,
        connection: Connection,
        run: StateRun,
        history: Sequence[StateEvent],
        proposals: Sequence[Proposal],
        now: str,
    ) -> None:
        """Writes one row per proposal. They take the commit time, or the newest `created_at` of the conversation's rows
        when the clock is behind it, so the stored order, by `created_at` then rowid, is the order the fold used."""
        newest = repository.last_created_at(connection, run.conversation_id)
        created_at = max(now, newest) if newest is not None else now
        rows = [
            StateEvent(
                id=new_id(),
                event_kind=proposal.event_kind,
                created_at=created_at,
                run_id=run.run_id,
                dimension=proposal.dimension,
                delta=proposal.delta,
                value_before=proposal.value_before,
                value_after=proposal.value_after,
            )
            for proposal in proposals
        ]
        changes = replay([*history, *rows], self._zone).changes
        for row, proposal in zip(rows, proposals, strict=True):
            value_before, value_after = row.value_before, row.value_after
            if row.event_kind == RELATIONSHIP_CHANGE:
                change = changes[row.id]
                value_before, value_after = str(change.before), str(change.after)
            repository.insert_event(
                connection,
                repository.NewStateRow(
                    id=row.id,
                    conversation_id=run.conversation_id,
                    character_id=run.character_id,
                    event_kind=row.event_kind,
                    dimension=row.dimension,
                    delta=row.delta,
                    value_before=value_before,
                    value_after=value_after,
                    reason=proposal.reason,
                    source_message_ids=proposal.sources,
                    run_id=run.run_id,
                    created_at=row.created_at,
                ),
            )

    def _store(self, connection: Connection, conversation_id: str, now: str) -> RelationshipState:
        """Replays the applied rows read back from the table, writes the result with the conversation's newest active
        episodes, and returns it. The narrative, expectations and boundaries come from the fold too, so they stay empty
        until the user's corrections and a scenario's starting state exist."""
        state = replay(repository.applied_events(connection, conversation_id), self._zone).state
        episodes = episode_repository.recent_episodes(connection, conversation_id, RECENT_EVENTS)
        repository.save_state(connection, conversation_id, state, [episode.id for episode in episodes], now)
        return state
