"""SQL of invalidation after a message change: the memories and episodes that cite a changed message, what the change
does to them, and the repairs that follow a memory's invalidation (a superseded predecessor or a lone conflict member).

A source "holds" while its message is visible and still shows the variant the source read (VALID_SOURCE). Every
function works on a connection the caller owns and never commits; InvalidationService runs them in the transaction of
the message change. Every change to a memory row records a revision by `consistency` first.
"""

from dataclasses import dataclass

from sqlalchemy import Connection, text

from lyrveil.memory.consistency import KnownMemory
from lyrveil.persistence.memory_extraction import VALID_SOURCE, drop_stale_memory_sources, known_memory, revise

_IN_USE = "('active', 'uncertain')"
_VALID_COUNT = (
    "SELECT count(*) FROM {sources} AS src JOIN messages AS msg ON msg.id = src.message_id "
    "WHERE src.{owner} = {row}.id AND " + VALID_SOURCE
)
# Driven by the message index of the sources table, so a change reads only the rows that cite the message.
_CITES_STALE = (
    "{row}.id IN (SELECT src.{owner} FROM {sources} AS src JOIN messages AS msg ON msg.id = src.message_id "
    "WHERE src.message_id = :message_id AND NOT (" + VALID_SOURCE + "))"
)


@dataclass(frozen=True)
class AffectedMemory:
    """A memory with a source the change made stale. `valid_sources` counts its sources that still hold."""

    id: str
    status: str
    locked: bool
    provenance: str
    confidence: float
    conflict_group_id: str | None
    valid_sources: int


@dataclass(frozen=True)
class AffectedEpisode:
    """An active episode with a source the change made stale. `valid_sources` counts its sources that still hold."""

    id: str
    locked: bool
    valid_sources: int


def affected_memories(connection: Connection, message_id: str) -> list[AffectedMemory]:
    """Returns the memories of any scope and status but `invalidated` that cite `message_id` through a source that no
    longer holds, by id."""
    valid = _VALID_COUNT.format(sources="memory_sources", owner="memory_id", row="m")
    stale = _CITES_STALE.format(sources="memory_sources", owner="memory_id", row="m")
    rows = (
        connection.execute(
            text(
                "SELECT m.id, m.status, m.locked, m.provenance, m.confidence, m.conflict_group_id, "
                f"({valid}) AS valid_sources FROM memories AS m WHERE m.status <> 'invalidated' AND {stale} "
                "ORDER BY m.id"
            ),
            {"message_id": message_id},
        )
        .mappings()
        .all()
    )
    return [
        AffectedMemory(
            id=row["id"],
            status=row["status"],
            locked=bool(row["locked"]),
            provenance=row["provenance"],
            confidence=row["confidence"],
            conflict_group_id=row["conflict_group_id"],
            valid_sources=row["valid_sources"],
        )
        for row in rows
    ]


def flag_for_review(connection: Connection, memory_id: str) -> None:
    """Sets `needs_review` and nothing else: no revision, no new version, no change of `updated_at`, because a locked
    memory is never changed by automation."""
    connection.execute(text("UPDATE memories SET needs_review = 1 WHERE id = :id"), {"id": memory_id})


def weaken_memory(connection: Connection, memory_id: str, confidence: float, *, reason: str, now: str) -> None:
    """Records a revision, removes the memory's sources that no longer hold and sets its confidence."""
    revise(connection, memory_id, actor="consistency", reason=reason, run_id=None, now=now)
    drop_stale_memory_sources(connection, memory_id)
    connection.execute(
        text("UPDATE memories SET confidence = :confidence WHERE id = :id"),
        {"id": memory_id, "confidence": confidence},
    )


def invalidate_memory(connection: Connection, memory_id: str, *, reason: str, now: str) -> None:
    """Records a revision and marks the memory `invalidated`. Its sources, links and index row stay, so a
    re-evaluation can bring the row back and the history keeps what it rested on."""
    revise(connection, memory_id, actor="consistency", reason=reason, run_id=None, now=now)
    connection.execute(text("UPDATE memories SET status = 'invalidated' WHERE id = :id"), {"id": memory_id})


def superseded_by(connection: Connection, memory_id: str) -> list[KnownMemory]:
    """Returns the `superseded` memories whose replacement is `memory_id`, oldest first."""
    ids = connection.execute(
        text("SELECT id FROM memories WHERE superseded_by_id = :id AND status = 'superseded' ORDER BY created_at, id"),
        {"id": memory_id},
    ).scalars()
    return [memory for memory in (known_memory(connection, found) for found in ids) if memory is not None]


def group_members_in_use(connection: Connection, conversation_id: str, group_id: str) -> list[KnownMemory]:
    """Returns the conversation's memories in use (active or uncertain) in the conflict group, oldest first."""
    ids = connection.execute(
        text(
            "SELECT id FROM memories WHERE conversation_id = :id AND conflict_group_id = :group_id "
            f"AND status IN {_IN_USE} ORDER BY created_at, id"
        ),
        {"id": conversation_id, "group_id": group_id},
    ).scalars()
    return [memory for memory in (known_memory(connection, found) for found in ids) if memory is not None]


def fingerprint_taken(connection: Connection, character_id: str, conversation_id: str, fingerprint: str) -> bool:
    """Tells whether a memory of the conversation in use has this fingerprint, which the unique index allows once."""
    return (
        connection.execute(
            text(
                "SELECT 1 FROM memories WHERE character_id = :character_id AND conversation_id = :id "
                f"AND fingerprint = :fingerprint AND status IN {_IN_USE} LIMIT 1"
            ),
            {"character_id": character_id, "id": conversation_id, "fingerprint": fingerprint},
        ).first()
        is not None
    )


def subject_values_in_use(connection: Connection, conversation_id: str, subject_key: str, exclude_id: str) -> list[str]:
    """Returns the values of the conversation's lasting memories in use with this subject, other than `exclude_id`
    and identity candidates: the values guard 3 keeps from coexisting."""
    return list(
        connection.execute(
            text(
                "SELECT value_text FROM memories WHERE conversation_id = :id AND subject_key = :subject "
                f"AND status IN {_IN_USE} AND durability = 'lasting' AND value_text IS NOT NULL "
                "AND kind <> 'identity_candidate' AND id <> :exclude"
            ),
            {"id": conversation_id, "subject": subject_key, "exclude": exclude_id},
        ).scalars()
    )


def restore_memory(connection: Connection, memory_id: str, *, reason: str, now: str) -> None:
    """Records a revision and makes a superseded memory `active` again: its `valid_to` and replacement link are
    cleared, since what replaced it no longer holds."""
    revise(connection, memory_id, actor="consistency", reason=reason, run_id=None, now=now)
    connection.execute(
        text("UPDATE memories SET status = 'active', valid_to = NULL, superseded_by_id = NULL WHERE id = :id"),
        {"id": memory_id},
    )


def leave_conflict(connection: Connection, memory_id: str, *, reason: str, now: str) -> None:
    """Records a revision and takes the memory out of its conflict group. An uncertain member becomes `active`,
    unless it waits for the user's review (`needs_review`)."""
    revise(connection, memory_id, actor="consistency", reason=reason, run_id=None, now=now)
    connection.execute(
        text(
            "UPDATE memories SET conflict_group_id = NULL, "
            "status = CASE WHEN status = 'uncertain' AND needs_review = 0 THEN 'active' ELSE status END WHERE id = :id"
        ),
        {"id": memory_id},
    )


def affected_episodes(connection: Connection, message_id: str) -> list[AffectedEpisode]:
    """Returns the active episodes that cite `message_id` through a source that no longer holds, by id."""
    valid = _VALID_COUNT.format(sources="episodic_sources", owner="episode_id", row="e")
    stale = _CITES_STALE.format(sources="episodic_sources", owner="episode_id", row="e")
    rows = (
        connection.execute(
            text(
                f"SELECT e.id, e.locked, ({valid}) AS valid_sources FROM episodic_memories AS e "
                f"WHERE e.status = 'active' AND {stale} ORDER BY e.id"
            ),
            {"message_id": message_id},
        )
        .mappings()
        .all()
    )
    return [AffectedEpisode(row["id"], bool(row["locked"]), row["valid_sources"]) for row in rows]


def invalidate_episode(connection: Connection, episode_id: str, now: str) -> None:
    """Marks the episode `invalidated`; its sources, links and index row stay."""
    connection.execute(
        text("UPDATE episodic_memories SET status = 'invalidated', updated_at = :now WHERE id = :id"),
        {"id": episode_id, "now": now},
    )
