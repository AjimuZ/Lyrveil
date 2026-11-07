"""SQL of progressive summaries: the cursor, the recent tail, the checkpoints and the conversation's summary jobs.

Every function works on a connection the caller owns and never commits. Writers open that connection with the WRITE
option of lyrveil.persistence.database, so the service decides what one transaction holds: a checkpoint and the cursor
are written together or not at all. A stored checkpoint is never rewritten: only its `status` changes (a trigger of
revision 0011 refuses the rest).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from lyrveil.memory.summaries import ACTIVE, INVALIDATED, SUPERSEDED, Checkpoint

_COLUMNS = "id, level, from_seq, to_seq, version, status, token_count, source_hash"


@dataclass(frozen=True)
class NewCheckpoint:
    """The columns of a checkpoint that a run inserts, with status `active`. `content` holds the lists of the
    SummaryResult (`events`, `decisions`, `relationship_changes`, `unresolved`, `key_facts`, `source_seqs`)."""

    id: str
    conversation_id: str
    level: int
    from_seq: int
    to_seq: int
    version: int
    content: dict[str, Any]
    text: str
    source_hash: str
    prompt_version: str
    token_count: int


@dataclass(frozen=True)
class StoredSummary:
    """A checkpoint with its prose and lists, as a rollup reads it."""

    checkpoint: Checkpoint
    text: str
    content: dict[str, Any]


def _checkpoint(row: RowMapping) -> Checkpoint:
    return Checkpoint(
        id=row["id"],
        level=row["level"],
        from_seq=row["from_seq"],
        to_seq=row["to_seq"],
        version=row["version"],
        status=row["status"],
        token_count=row["token_count"],
        source_hash=row["source_hash"],
    )


def cursor(connection: Connection, conversation_id: str) -> int:
    """Returns `last_summarized_seq`, the seq of the last message a level-0 checkpoint covers (0 before the first). The
    conversation must exist."""
    value: int = connection.execute(
        text("SELECT last_summarized_seq FROM conversations WHERE id = :id"), {"id": conversation_id}
    ).scalar_one()
    return value


def advance_cursor(connection: Connection, conversation_id: str, to_seq: int) -> None:
    """Moves `last_summarized_seq` to `to_seq` when that is further; it never moves back. `updated_at` is left alone,
    because the cursor is not a change the user sees."""
    connection.execute(
        text("UPDATE conversations SET last_summarized_seq = max(last_summarized_seq, :seq) WHERE id = :id"),
        {"seq": to_seq, "id": conversation_id},
    )


def tail_start(connection: Connection, conversation_id: str, tail_messages: int) -> int | None:
    """Returns the seq of the oldest of the conversation's newest `tail_messages` readable messages (not deleted, not a
    system message, complete or interrupted, with text), or None when it has no more than that many."""
    found: int | None = connection.execute(
        text(
            "SELECT m.seq FROM messages AS m JOIN message_variants AS v ON v.id = m.active_variant_id "
            "WHERE m.conversation_id = :id AND m.deleted_at IS NULL AND m.author != 'system' "
            "AND m.status IN ('complete', 'interrupted') AND v.content != '' "
            "ORDER BY m.seq DESC LIMIT 1 OFFSET :skip"
        ),
        {"id": conversation_id, "skip": tail_messages - 1},
    ).scalar()
    return found


def checkpoints(connection: Connection, conversation_id: str) -> list[Checkpoint]:
    """Returns every checkpoint of the conversation in any status, oldest range first, lowest level first, then by
    version."""
    rows = (
        connection.execute(
            text(
                f"SELECT {_COLUMNS} FROM conversation_summaries WHERE conversation_id = :id "
                "ORDER BY from_seq, level, to_seq, version"
            ),
            {"id": conversation_id},
        )
        .mappings()
        .all()
    )
    return [_checkpoint(row) for row in rows]


def active_summaries(connection: Connection, conversation_id: str) -> list[StoredSummary]:
    """Returns the conversation's `active` checkpoints with their prose and lists, in the order of `checkpoints`: the
    candidates of a reply prompt (docs/memory-engine.md, Choosing summaries for a prompt)."""
    rows = (
        connection.execute(
            text(
                f"SELECT {_COLUMNS}, text, content_json FROM conversation_summaries "
                "WHERE conversation_id = :id AND status = :active ORDER BY from_seq, level, to_seq, version"
            ),
            {"id": conversation_id, "active": ACTIVE},
        )
        .mappings()
        .all()
    )
    return [StoredSummary(_checkpoint(row), row["text"], json.loads(row["content_json"])) for row in rows]


def stored_summaries(connection: Connection, ids: Sequence[str]) -> list[StoredSummary]:
    """Returns the checkpoints `ids` with their prose and lists, in the order of `ids`. Raises KeyError for an id that
    does not exist."""
    found: dict[str, StoredSummary] = {}
    for checkpoint_id in ids:
        row = (
            connection.execute(
                text(f"SELECT {_COLUMNS}, text, content_json FROM conversation_summaries WHERE id = :id"),
                {"id": checkpoint_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise KeyError(checkpoint_id)
        found[checkpoint_id] = StoredSummary(_checkpoint(row), row["text"], json.loads(row["content_json"]))
    return [found[checkpoint_id] for checkpoint_id in ids]


def next_version(connection: Connection, conversation_id: str, level: int, from_seq: int, to_seq: int) -> int:
    """Returns the version a new checkpoint of this exact range takes: one above the highest stored, 1 for a first."""
    highest: int | None = connection.execute(
        text(
            "SELECT max(version) FROM conversation_summaries WHERE conversation_id = :id AND level = :level "
            "AND from_seq = :from_seq AND to_seq = :to_seq"
        ),
        {"id": conversation_id, "level": level, "from_seq": from_seq, "to_seq": to_seq},
    ).scalar()
    return 1 if highest is None else highest + 1


def insert_checkpoint(connection: Connection, checkpoint: NewCheckpoint, now: str) -> None:
    """Adds an `active` checkpoint. Raises IntegrityError when the range already has that version. The caller marks
    the version it replaces with supersede."""
    connection.execute(
        text(
            "INSERT INTO conversation_summaries (id, conversation_id, level, from_seq, to_seq, version, status, "
            "content_json, text, source_hash, prompt_version, token_count, created_at) VALUES (:id, :conversation_id, "
            ":level, :from_seq, :to_seq, :version, 'active', :content, :text, :source_hash, :prompt_version, "
            ":token_count, :now)"
        ),
        {
            "id": checkpoint.id,
            "conversation_id": checkpoint.conversation_id,
            "level": checkpoint.level,
            "from_seq": checkpoint.from_seq,
            "to_seq": checkpoint.to_seq,
            "version": checkpoint.version,
            "content": json.dumps(checkpoint.content, ensure_ascii=False),
            "text": checkpoint.text,
            "source_hash": checkpoint.source_hash,
            "prompt_version": checkpoint.prompt_version,
            "token_count": checkpoint.token_count,
            "now": now,
        },
    )


def supersede(connection: Connection, conversation_id: str, level: int, from_seq: int, to_seq: int) -> int:
    """Marks `superseded` the active checkpoints of exactly this range and returns how many there were. Nothing else
    about them changes."""
    result = connection.execute(
        text(
            f"UPDATE conversation_summaries SET status = '{SUPERSEDED}' WHERE conversation_id = :id AND level = :level "
            f"AND from_seq = :from_seq AND to_seq = :to_seq AND status = '{ACTIVE}'"
        ),
        {"id": conversation_id, "level": level, "from_seq": from_seq, "to_seq": to_seq},
    )
    return int(result.rowcount)


def invalidate_covering(connection: Connection, conversation_id: str, seq: int) -> int:
    """Marks `invalidated` the active checkpoints of the conversation, of every level, whose range holds `seq`, and
    returns how many there were. Nothing else about them changes."""
    result = connection.execute(
        text(
            f"UPDATE conversation_summaries SET status = '{INVALIDATED}' WHERE conversation_id = :id "
            f"AND from_seq <= :seq AND to_seq >= :seq AND status = '{ACTIVE}'"
        ),
        {"id": conversation_id, "seq": seq},
    )
    return int(result.rowcount)


def invalidated_ranges(connection: Connection, conversation_id: str, seq: int) -> list[tuple[int, int]]:
    """Returns the level-0 ranges `(from_seq, to_seq)` of the conversation that hold `seq` and whose newest version is
    `invalidated`: those that need summarizing again. Rollups are not listed."""
    rows = connection.execute(
        text(
            "SELECT DISTINCT c.from_seq, c.to_seq FROM conversation_summaries AS c WHERE c.conversation_id = :id "
            f"AND c.level = 0 AND c.from_seq <= :seq AND c.to_seq >= :seq AND c.status = '{INVALIDATED}' "
            "AND c.version = (SELECT max(o.version) FROM conversation_summaries AS o "
            "WHERE o.conversation_id = c.conversation_id AND o.level = 0 AND o.from_seq = c.from_seq "
            "AND o.to_seq = c.to_seq) ORDER BY c.from_seq"
        ),
        {"id": conversation_id, "seq": seq},
    ).all()
    return [(first, last) for first, last in rows]


def has_active_summary_job(connection: Connection, conversation_id: str, exclude_job_id: str | None) -> bool:
    """Tells whether a `summary_checkpoint` job of the conversation is queued or running, other than
    `exclude_job_id`."""
    return (
        connection.execute(
            text(
                "SELECT 1 FROM generation_jobs WHERE kind = 'summary_checkpoint' AND conversation_id = :id "
                "AND status IN ('queued', 'running') AND (:exclude IS NULL OR id != :exclude) LIMIT 1"
            ),
            {"id": conversation_id, "exclude": exclude_job_id},
        ).first()
        is not None
    )
