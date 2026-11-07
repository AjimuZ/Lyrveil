"""SQL of the Character State Manager: the applied rows of `character_state_history` in the order they were written, new
rows, their invalidation by source message, and the one `relationship_states` row of a conversation.

Every function works on a connection the caller owns and never commits, and reads or writes the rows of one
conversation only.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, RowMapping, bindparam, text

from lyrveil.consistency.state import APPLIED, INVALIDATED, RelationshipState, StateEvent


@dataclass(frozen=True)
class NewStateRow:
    """The columns of one `character_state_history` row, written `applied`. `delta` is the requested delta, and
    `value_before` and `value_after` what the fold made of it, so the row records both (docs/consistency-engine.md,
    Update rules)."""

    id: str
    conversation_id: str
    character_id: str
    event_kind: str
    dimension: str | None
    delta: int | None
    value_before: str | None
    value_after: str | None
    reason: str
    source_message_ids: Sequence[str]
    run_id: str | None
    created_at: str


@dataclass(frozen=True)
class StoredState:
    """The `relationship_states` row of a conversation: its state, the newest episodes and when it was written."""

    state: RelationshipState
    recent_event_ids: tuple[str, ...]
    updated_at: str


def _event(row: RowMapping) -> StateEvent:
    return StateEvent(
        id=row["id"],
        event_kind=row["event_kind"],
        created_at=row["created_at"],
        run_id=row["run_id"],
        dimension=row["dimension"],
        delta=row["delta"],
        value_before=row["value_before"],
        value_after=row["value_after"],
    )


def applied_events(connection: Connection, conversation_id: str) -> list[StateEvent]:
    """Returns the applied history rows of the conversation in the order they were written: by `created_at`, then by
    rowid, which follows the insertion order of the rows of one run, since they share their `created_at`."""
    rows = (
        connection.execute(
            text(
                "SELECT id, event_kind, dimension, delta, value_before, value_after, run_id, created_at "
                "FROM character_state_history WHERE conversation_id = :id AND status = :applied "
                "ORDER BY created_at, rowid"
            ),
            {"id": conversation_id, "applied": APPLIED},
        )
        .mappings()
        .all()
    )
    return [_event(row) for row in rows]


def last_created_at(connection: Connection, conversation_id: str) -> str | None:
    """Returns the newest `created_at` of the conversation's history rows in any status, or None without one."""
    value: str | None = connection.execute(
        text("SELECT max(created_at) FROM character_state_history WHERE conversation_id = :id"),
        {"id": conversation_id},
    ).scalar_one()
    return value


def insert_event(connection: Connection, row: NewStateRow) -> None:
    """Writes one `applied` history row."""
    connection.execute(
        text(
            "INSERT INTO character_state_history (id, conversation_id, character_id, event_kind, dimension, delta, "
            "value_before, value_after, reason, source_message_ids_json, run_id, status, created_at) VALUES (:id, "
            ":conversation_id, :character_id, :event_kind, :dimension, :delta, :value_before, :value_after, :reason, "
            ":sources, :run_id, :applied, :created_at)"
        ),
        {
            "id": row.id,
            "conversation_id": row.conversation_id,
            "character_id": row.character_id,
            "event_kind": row.event_kind,
            "dimension": row.dimension,
            "delta": row.delta,
            "value_before": row.value_before,
            "value_after": row.value_after,
            "reason": row.reason,
            "sources": json.dumps(list(row.source_message_ids)),
            "run_id": row.run_id,
            "applied": APPLIED,
            "created_at": row.created_at,
        },
    )


def invalidate_citing(connection: Connection, conversation_id: str, message_ids: Sequence[str]) -> int:
    """Marks `invalidated` the applied rows of the conversation whose sources name one of `message_ids`, and returns
    how many it changed. Rows of other conversations are never read."""
    if not message_ids:
        return 0
    statement = text(
        "UPDATE character_state_history SET status = :invalidated "
        "WHERE conversation_id = :id AND status = :applied AND EXISTS ("
        "SELECT 1 FROM json_each(character_state_history.source_message_ids_json) AS source "
        "WHERE source.value IN :message_ids)"
    ).bindparams(bindparam("message_ids", expanding=True))
    result = connection.execute(
        statement,
        {"invalidated": INVALIDATED, "applied": APPLIED, "id": conversation_id, "message_ids": list(message_ids)},
    )
    return int(result.rowcount)


def load_state(connection: Connection, conversation_id: str) -> StoredState | None:
    """Returns the conversation's `relationship_states` row, or None when the state was never written."""
    row = (
        connection.execute(
            text("SELECT * FROM relationship_states WHERE conversation_id = :id"), {"id": conversation_id}
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    state = RelationshipState(
        familiarity=row["familiarity"],
        trust=row["trust"],
        confidence=row["confidence"],
        tension=row["tension"],
        narrative=row["narrative"],
        expectations=row["expectations"],
        boundaries=row["boundaries"],
        emotional_state=row["emotional_state"],
        current_topic=row["current_topic"],
        open_threads=tuple(json.loads(row["open_threads_json"])),
        goals=tuple(json.loads(row["goals_json"])),
        narrative_changes=tuple(json.loads(row["narrative_changes_json"])),
        last_history_id=row["last_history_id"],
    )
    return StoredState(state, tuple(json.loads(row["recent_event_ids_json"])), row["updated_at"])


def save_state(
    connection: Connection, conversation_id: str, state: RelationshipState, recent_event_ids: Sequence[str], now: str
) -> None:
    """Writes the conversation's `relationship_states` row, inserting it the first time and replacing every column
    after that."""
    connection.execute(
        text(
            "INSERT INTO relationship_states (conversation_id, familiarity, trust, confidence, tension, narrative, "
            "expectations, boundaries, emotional_state, current_topic, open_threads_json, goals_json, "
            "recent_event_ids_json, narrative_changes_json, last_history_id, updated_at) VALUES (:id, :familiarity, "
            ":trust, :confidence, :tension, :narrative, :expectations, :boundaries, :emotional_state, :current_topic, "
            ":open_threads, :goals, :recent_events, :narrative_changes, :last_history_id, :now) "
            "ON CONFLICT (conversation_id) DO UPDATE SET familiarity = excluded.familiarity, trust = excluded.trust, "
            "confidence = excluded.confidence, tension = excluded.tension, narrative = excluded.narrative, "
            "expectations = excluded.expectations, boundaries = excluded.boundaries, "
            "emotional_state = excluded.emotional_state, current_topic = excluded.current_topic, "
            "open_threads_json = excluded.open_threads_json, goals_json = excluded.goals_json, "
            "recent_event_ids_json = excluded.recent_event_ids_json, "
            "narrative_changes_json = excluded.narrative_changes_json, last_history_id = excluded.last_history_id, "
            "updated_at = excluded.updated_at"
        ),
        {
            "id": conversation_id,
            "familiarity": state.familiarity,
            "trust": state.trust,
            "confidence": state.confidence,
            "tension": state.tension,
            "narrative": state.narrative,
            "expectations": state.expectations,
            "boundaries": state.boundaries,
            "emotional_state": state.emotional_state,
            "current_topic": state.current_topic,
            "open_threads": json.dumps(list(state.open_threads), ensure_ascii=False),
            "goals": json.dumps(list(state.goals), ensure_ascii=False),
            "recent_events": json.dumps(list(recent_event_ids)),
            "narrative_changes": json.dumps(list(state.narrative_changes), ensure_ascii=False),
            "last_history_id": state.last_history_id,
            "now": now,
        },
    )
