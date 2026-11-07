"""SQL of memory extraction: the messages of a window, the cursor, the runs, the memories and episodes a run shows and
writes, the changes the consistency rules make with their revisions, the tombstones that block a candidate, and the
conversation's extraction jobs.

Every function works on a connection the caller owns and never commits. Writers open that connection with the WRITE
option of lyrveil.persistence.database, so the service decides what one transaction holds: a run's memories, sources,
index rows, revisions, episodes, its own row and the cursor are written together or not at all.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.identifiers import new_id
from lyrveil.domain.messages import Author, MessageStatus
from lyrveil.memory.consistency import KnownMemory, Tombstone
from lyrveil.memory.window import Window, WindowMessage

_WINDOW_SELECT = """
SELECT m.id, m.seq, m.author, m.status, m.deleted_at, m.created_at, v.id AS variant_id, v.content, v.content_hash
FROM messages AS m LEFT JOIN message_variants AS v ON v.id = m.active_variant_id
"""
# The memories in use: those of the conversation that prompts use and the fingerprint index keeps unique.
_SHOWN_STATUSES = "('active', 'uncertain')"
# The memories a run shows: in use, and never an `identity_candidate`, which no prompt shows
# (docs/consistency-engine.md, Rules and enforcement).
_SHOWN = f"m.status IN {_SHOWN_STATUSES} AND m.kind <> 'identity_candidate'"
_KNOWN_COLUMNS = """
m.id, m.kind, m.content, m.subject_key, m.value_text, m.provenance, m.durability, m.status, m.importance,
m.locked, m.conflict_group_id, m.fingerprint,
(SELECT max(msg.seq) FROM memory_sources AS s JOIN messages AS msg ON msg.id = s.message_id WHERE s.memory_id = m.id)
AS source_max_seq
"""
_KNOWN_SELECT = f"SELECT {_KNOWN_COLUMNS} FROM memories AS m"


@dataclass(frozen=True)
class Run:
    """One row of `memory_extraction_runs`, as the pipeline needs it. `status` is running, completed, failed or
    superseded."""

    id: str
    status: str
    attempt: int
    from_seq: int
    to_seq: int


@dataclass(frozen=True)
class ShownMemory:
    """An existing memory a run shows to the model (as `E1`...). `status` is `active` or `uncertain`."""

    id: str
    content: str
    status: str


@dataclass(frozen=True)
class ShownEpisode:
    """A recent episode a run shows to the model (as `P1`...) or finds by fingerprint."""

    id: str
    title: str


@dataclass(frozen=True)
class NewMemory:
    """The columns of a conversation memory that extraction inserts, at version 1. `status` is `active` or
    `uncertain`."""

    id: str
    character_id: str
    conversation_id: str
    kind: str
    content: str
    subject_key: str | None
    value_text: str | None
    provenance: str
    durability: str
    importance: int
    confidence: float
    valid_from: str
    expires_at: str | None
    fingerprint: str
    language: str
    status: str = "active"
    needs_review: bool = False
    conflict_group_id: str | None = None


@dataclass(frozen=True)
class NewEpisode:
    """The columns of an episode that extraction inserts. Status is `active`."""

    id: str
    character_id: str
    conversation_id: str
    title: str
    description: str
    occurred_at: str
    participants: Sequence[str]
    importance: int
    fingerprint: str


def _window_message(row: RowMapping) -> WindowMessage:
    return WindowMessage(
        message_id=row["id"],
        seq=row["seq"],
        author=Author(row["author"]),
        status=MessageStatus(row["status"]),
        deleted=row["deleted_at"] is not None,
        content=row["content"] or "",
        variant_id=row["variant_id"],
        content_hash=row["content_hash"],
        created_at=row["created_at"],
    )


def cursor(connection: Connection, conversation_id: str) -> int:
    """Returns `last_extracted_seq`, the seq of the last message extraction has passed (0 before the first run). The
    conversation must exist."""
    value: int = connection.execute(
        text("SELECT last_extracted_seq FROM conversations WHERE id = :id"), {"id": conversation_id}
    ).scalar_one()
    return value


def advance_cursor(connection: Connection, conversation_id: str, to_seq: int) -> None:
    """Moves `last_extracted_seq` to `to_seq` when that is further; it never moves back. `updated_at` is left alone,
    because the cursor is not a change the user sees."""
    connection.execute(
        text("UPDATE conversations SET last_extracted_seq = max(last_extracted_seq, :seq) WHERE id = :id"),
        {"seq": to_seq, "id": conversation_id},
    )


def messages_after(
    connection: Connection, conversation_id: str, after_seq: int, *, limit: int, up_to_seq: int | None = None
) -> list[WindowMessage]:
    """Returns up to `limit` messages of the conversation with a seq above `after_seq` (and at most `up_to_seq` when
    given), in seq order and in any state, deleted included, each with its active variant."""
    clause = "" if up_to_seq is None else "AND m.seq <= :up_to"
    rows = (
        connection.execute(
            text(
                f"{_WINDOW_SELECT} WHERE m.conversation_id = :id AND m.seq > :after {clause} "
                "ORDER BY m.seq LIMIT :limit"
            ),
            {"id": conversation_id, "after": after_seq, "up_to": up_to_seq, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_window_message(row) for row in rows]


def context_before(connection: Connection, conversation_id: str, before_seq: int, limit: int) -> list[WindowMessage]:
    """Returns the newest `limit` messages below `before_seq` that a window could read (not deleted, not a system
    message, complete or interrupted, with text), oldest first."""
    rows = (
        connection.execute(
            text(
                f"{_WINDOW_SELECT} WHERE m.conversation_id = :id AND m.seq < :before AND m.deleted_at IS NULL "
                "AND m.author != 'system' AND m.status IN ('complete', 'interrupted') AND v.content != '' "
                "ORDER BY m.seq DESC LIMIT :limit"
            ),
            {"id": conversation_id, "before": before_seq, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_window_message(row) for row in reversed(rows)]


def find_run(
    connection: Connection, conversation_id: str, window_hash: str, prompt_version: str, schema_version: str
) -> Run | None:
    """Returns the run of this window under this prompt and schema, the idempotency key, or None."""
    row = (
        connection.execute(
            text(
                "SELECT id, status, attempt, from_seq, to_seq FROM memory_extraction_runs WHERE conversation_id = :id "
                "AND window_hash = :hash AND prompt_version = :prompt AND schema_version = :schema"
            ),
            {"id": conversation_id, "hash": window_hash, "prompt": prompt_version, "schema": schema_version},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return Run(
        id=row["id"], status=row["status"], attempt=row["attempt"], from_seq=row["from_seq"], to_seq=row["to_seq"]
    )


def insert_run(
    connection: Connection,
    *,
    run_id: str,
    conversation_id: str,
    window: Window,
    prompt_version: str,
    schema_version: str,
    now: str,
) -> None:
    """Adds the `running` run of a window, at attempt 1."""
    connection.execute(
        text(
            "INSERT INTO memory_extraction_runs (id, conversation_id, from_seq, to_seq, window_hash, prompt_version, "
            "schema_version, status, attempt, created_at) VALUES (:id, :conversation_id, :from_seq, :to_seq, :hash, "
            ":prompt, :schema, 'running', 1, :now)"
        ),
        {
            "id": run_id,
            "conversation_id": conversation_id,
            "from_seq": window.from_seq,
            "to_seq": window.to_seq,
            "hash": window.hash,
            "prompt": prompt_version,
            "schema": schema_version,
            "now": now,
        },
    )


def restart_run(connection: Connection, run_id: str, window: Window) -> None:
    """Marks a failed, superseded or interrupted run `running` again for its next attempt, with the window's range
    (the same messages may now follow another cursor). The output and error of the previous attempt are cleared."""
    connection.execute(
        text(
            "UPDATE memory_extraction_runs SET status = 'running', attempt = attempt + 1, from_seq = :from_seq, "
            "to_seq = :to_seq, raw_output = NULL, error_code = NULL, finished_at = NULL WHERE id = :id"
        ),
        {"id": run_id, "from_seq": window.from_seq, "to_seq": window.to_seq},
    )


def finish_run(
    connection: Connection, run_id: str, status: str, *, raw_output: str | None, error_code: str | None, now: str
) -> None:
    """Ends a run as `completed`, `failed` or `superseded`, with the model's text (kept locally for debugging) and the
    error code of a failure."""
    connection.execute(
        text(
            "UPDATE memory_extraction_runs SET status = :status, raw_output = :raw, error_code = :code, "
            "finished_at = :now WHERE id = :id"
        ),
        {"id": run_id, "status": status, "raw": raw_output, "code": error_code, "now": now},
    )


def _shown_memory(row: RowMapping) -> ShownMemory:
    return ShownMemory(id=row["id"], content=row["content"], status=row["status"])


def related_memories(connection: Connection, conversation_id: str, match: str, limit: int) -> list[ShownMemory]:
    """Returns up to `limit` memories of the conversation a run shows (active or uncertain, no `identity_candidate`)
    whose index row matches `match` (from lyrveil.memory.text.match_query), best match first."""
    rows = (
        connection.execute(
            text(
                "SELECT m.id, m.content, m.status FROM memory_fts JOIN memories AS m "
                "ON m.id = memory_fts.memory_id WHERE memory_fts MATCH :match AND m.conversation_id = :id "
                f"AND {_SHOWN} ORDER BY bm25(memory_fts, 0.0, 2.0, 1.0), m.id LIMIT :limit"
            ),
            {"match": match, "id": conversation_id, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_shown_memory(row) for row in rows]


def recent_memories(connection: Connection, conversation_id: str, limit: int) -> list[ShownMemory]:
    """Returns up to `limit` memories of the conversation a run shows, the most recently changed first."""
    rows = (
        connection.execute(
            text(
                f"SELECT m.id, m.content, m.status FROM memories AS m WHERE m.conversation_id = :id AND {_SHOWN} "
                "ORDER BY m.updated_at DESC, m.id DESC LIMIT :limit"
            ),
            {"id": conversation_id, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_shown_memory(row) for row in rows]


def _known_memory(row: RowMapping) -> KnownMemory:
    return KnownMemory(
        id=row["id"],
        kind=row["kind"],
        content=row["content"],
        subject_key=row["subject_key"],
        value_text=row["value_text"],
        provenance=row["provenance"],
        durability=row["durability"],
        status=row["status"],
        importance=row["importance"],
        locked=bool(row["locked"]),
        conflict_group_id=row["conflict_group_id"],
        fingerprint=row["fingerprint"],
        source_max_seq=row["source_max_seq"],
    )


def known_memory(connection: Connection, memory_id: str) -> KnownMemory | None:
    """Returns the memory as the consistency rules see it, in any status, or None when it does not exist."""
    row = connection.execute(text(f"{_KNOWN_SELECT} WHERE m.id = :id"), {"id": memory_id}).mappings().first()
    return None if row is None else _known_memory(row)


def known_by_fingerprint(
    connection: Connection, character_id: str, conversation_id: str, fingerprint: str
) -> KnownMemory | None:
    """Returns the memory of the conversation in use (active or uncertain) with this fingerprint, the one the unique
    index allows, of any kind, or None."""
    row = (
        connection.execute(
            text(
                f"{_KNOWN_SELECT} WHERE m.character_id = :character_id AND m.conversation_id = :id "
                f"AND m.fingerprint = :fingerprint AND m.status IN {_SHOWN_STATUSES}"
            ),
            {"character_id": character_id, "id": conversation_id, "fingerprint": fingerprint},
        )
        .mappings()
        .first()
    )
    return None if row is None else _known_memory(row)


def similar_candidates(
    connection: Connection, conversation_id: str, kind: str, subject: str | None, match: str, limit: int
) -> list[KnownMemory]:
    """Returns up to `limit` memories of the conversation in use, of this kind and subject (both None counts as the
    same), whose index row matches `match`, best match first: the only memories that can be near duplicates, since
    those share most stems."""
    rows = (
        connection.execute(
            text(
                f"SELECT {_KNOWN_COLUMNS} FROM memory_fts JOIN memories AS m ON m.id = memory_fts.memory_id "
                "WHERE memory_fts MATCH :match AND m.conversation_id = :id AND m.kind = :kind "
                f"AND m.subject_key IS :subject AND m.status IN {_SHOWN_STATUSES} "
                "ORDER BY bm25(memory_fts, 0.0, 2.0, 1.0), m.id LIMIT :limit"
            ),
            {"match": match, "id": conversation_id, "kind": kind, "subject": subject, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_known_memory(row) for row in rows]


def subject_rivals(connection: Connection, conversation_id: str, subject: str) -> list[KnownMemory]:
    """Returns the active lasting memories of the conversation with this subject and a value, other than identity
    candidates: those guard 3 compares a new value with."""
    rows = (
        connection.execute(
            text(
                f"{_KNOWN_SELECT} WHERE m.conversation_id = :id AND m.subject_key = :subject AND m.status = 'active' "
                "AND m.durability = 'lasting' AND m.value_text IS NOT NULL AND m.kind <> 'identity_candidate' "
                "ORDER BY m.created_at, m.id"
            ),
            {"id": conversation_id, "subject": subject},
        )
        .mappings()
        .all()
    )
    return [_known_memory(row) for row in rows]


def matching_tombstones(
    connection: Connection,
    character_id: str,
    conversation_id: str,
    fingerprint: str,
    subject: str | None,
    value_hash: str | None,
) -> list[Tombstone]:
    """Returns the tombstones of the character's conversation that can block a candidate: those with its fingerprint,
    and those with its subject key and value hash. The comparison with the sources is `consistency.is_blocked`."""
    rows = (
        connection.execute(
            text(
                "SELECT fingerprint, subject_key, value_hash, source_hashes_json, source_max_seq "
                "FROM memory_tombstones WHERE character_id = :character_id AND conversation_id = :id "
                "AND (fingerprint = :fingerprint OR "
                "(subject_key IS NOT NULL AND subject_key IS :subject AND value_hash IS NOT NULL "
                "AND value_hash IS :value_hash))"
            ),
            {
                "character_id": character_id,
                "id": conversation_id,
                "fingerprint": fingerprint,
                "subject": subject,
                "value_hash": value_hash,
            },
        )
        .mappings()
        .all()
    )
    return [
        Tombstone(
            fingerprint=row["fingerprint"],
            subject_key=row["subject_key"],
            value_hash=row["value_hash"],
            source_hashes=frozenset(json.loads(row["source_hashes_json"])),
            source_max_seq=row["source_max_seq"],
        )
        for row in rows
    ]


def insert_memory(connection: Connection, memory: NewMemory, now: str) -> None:
    """Adds a conversation memory at version 1 with the status of `memory`. Its sources and index row are the
    caller's to add."""
    connection.execute(
        text(
            "INSERT INTO memories (id, character_id, scope, conversation_id, kind, content, subject_key, value_text, "
            "provenance, durability, status, importance, confidence, needs_review, valid_from, expires_at, "
            "conflict_group_id, fingerprint, language, created_at, updated_at) VALUES (:id, :character_id, "
            "'conversation', :conversation_id, :kind, :content, :subject_key, :value_text, :provenance, :durability, "
            ":status, :importance, :confidence, :needs_review, :valid_from, :expires_at, :conflict_group_id, "
            ":fingerprint, :language, :now, :now)"
        ),
        {
            "id": memory.id,
            "character_id": memory.character_id,
            "conversation_id": memory.conversation_id,
            "kind": memory.kind,
            "content": memory.content,
            "subject_key": memory.subject_key,
            "value_text": memory.value_text,
            "provenance": memory.provenance,
            "durability": memory.durability,
            "status": memory.status,
            "importance": memory.importance,
            "confidence": memory.confidence,
            "needs_review": int(memory.needs_review),
            "valid_from": memory.valid_from,
            "expires_at": memory.expires_at,
            "conflict_group_id": memory.conflict_group_id,
            "fingerprint": memory.fingerprint,
            "language": memory.language,
            "now": now,
        },
    )


def raise_memory_importance(connection: Connection, memory_id: str, importance: int, now: str) -> None:
    """Raises the memory's importance to `importance` when that is higher; its version stays, as for a duplicate."""
    connection.execute(
        text(
            "UPDATE memories SET importance = :importance, updated_at = :now "
            "WHERE id = :id AND importance < :importance"
        ),
        {"id": memory_id, "importance": importance, "now": now},
    )


def revise(connection: Connection, memory_id: str, *, actor: str, reason: str, run_id: str | None, now: str) -> None:
    """Appends the memory's row as it is now to `memory_revisions`, under its current version, and moves the memory
    to the next version. The caller changes the row right after, in the same transaction. `actor` is `extraction`,
    `user` or `consistency`."""
    row = connection.execute(text("SELECT * FROM memories WHERE id = :id"), {"id": memory_id}).mappings().one()
    connection.execute(
        text(
            "INSERT INTO memory_revisions (id, memory_id, version, snapshot_json, actor, reason, run_id, created_at) "
            "VALUES (:id, :memory_id, :version, :snapshot, :actor, :reason, :run_id, :now)"
        ),
        {
            "id": new_id(),
            "memory_id": memory_id,
            "version": row["version"],
            "snapshot": json.dumps(dict(row), ensure_ascii=False),
            "actor": actor,
            "reason": reason,
            "run_id": run_id,
            "now": now,
        },
    )
    connection.execute(
        text("UPDATE memories SET version = version + 1, updated_at = :now WHERE id = :id"),
        {"id": memory_id, "now": now},
    )


def supersede_memory(
    connection: Connection, memory_id: str, *, valid_to: str, reason: str, run_id: str | None, now: str
) -> None:
    """Records a revision and marks the memory `superseded`, valid until `valid_to`. Its sources stay. Call
    link_superseded once the memory that replaces it exists."""
    revise(connection, memory_id, actor="extraction", reason=reason, run_id=run_id, now=now)
    connection.execute(
        text("UPDATE memories SET status = 'superseded', valid_to = :valid_to WHERE id = :id"),
        {"id": memory_id, "valid_to": valid_to},
    )


def link_superseded(connection: Connection, memory_ids: Sequence[str], replacement_id: str) -> None:
    """Points each superseded memory at the memory that replaced it; part of the change supersede_memory recorded."""
    for memory_id in memory_ids:
        connection.execute(
            text("UPDATE memories SET superseded_by_id = :replacement WHERE id = :id"),
            {"id": memory_id, "replacement": replacement_id},
        )


def join_conflict(
    connection: Connection,
    memory_id: str,
    group_id: str,
    *,
    uncertain: bool,
    reason: str,
    run_id: str | None,
    now: str,
) -> None:
    """Records a revision and puts the memory in the conflict group, `uncertain` when asked, otherwise with its status
    kept."""
    revise(connection, memory_id, actor="extraction", reason=reason, run_id=run_id, now=now)
    connection.execute(
        text(
            "UPDATE memories SET conflict_group_id = :group_id, "
            "status = CASE WHEN :uncertain THEN 'uncertain' ELSE status END WHERE id = :id"
        ),
        {"id": memory_id, "group_id": group_id, "uncertain": int(uncertain)},
    )


def expire_memories(connection: Connection, conversation_id: str, reason: str, now: str) -> list[str]:
    """Marks `expired`, each with a revision by `consistency`, the temporary memories of the conversation in use whose
    `expires_at` is at or before `now`, and returns their ids."""
    expired = list(
        connection.execute(
            text(
                "SELECT id FROM memories WHERE conversation_id = :id AND durability = 'temporary' "
                f"AND status IN {_SHOWN_STATUSES} AND expires_at <= :now ORDER BY id"
            ),
            {"id": conversation_id, "now": now},
        ).scalars()
    )
    for memory_id in expired:
        revise(connection, memory_id, actor="consistency", reason=reason, run_id=None, now=now)
        connection.execute(text("UPDATE memories SET status = 'expired' WHERE id = :id"), {"id": memory_id})
    return expired


def add_memory_sources(connection: Connection, memory_id: str, sources: Sequence[WindowMessage]) -> int:
    """Records each message, with its active variant and that variant's content hash, as a source of the memory, and
    returns how many were new; a source already recorded is kept as it is."""
    added = 0
    for source in sources:
        result = connection.execute(
            text(
                "INSERT OR IGNORE INTO memory_sources (memory_id, message_id, variant_id, content_hash) "
                "VALUES (:memory_id, :message_id, :variant_id, :content_hash)"
            ),
            {
                "memory_id": memory_id,
                "message_id": source.message_id,
                "variant_id": source.variant_id,
                "content_hash": source.content_hash,
            },
        )
        added += result.rowcount
    return added


def recent_episodes(connection: Connection, conversation_id: str, limit: int) -> list[ShownEpisode]:
    """Returns up to `limit` active episodes of the conversation, the most recent first."""
    rows = (
        connection.execute(
            text(
                "SELECT id, title FROM episodic_memories WHERE conversation_id = :id AND status = 'active' "
                "ORDER BY occurred_at DESC, id DESC LIMIT :limit"
            ),
            {"id": conversation_id, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [ShownEpisode(id=row["id"], title=row["title"]) for row in rows]


def find_episode_by_fingerprint(connection: Connection, conversation_id: str, fingerprint: str) -> ShownEpisode | None:
    """Returns the active episode of the conversation with this fingerprint, the one the unique index allows, or
    None."""
    row = (
        connection.execute(
            text(
                "SELECT id, title FROM episodic_memories WHERE conversation_id = :id AND fingerprint = :fingerprint "
                "AND status = 'active'"
            ),
            {"id": conversation_id, "fingerprint": fingerprint},
        )
        .mappings()
        .first()
    )
    return None if row is None else ShownEpisode(id=row["id"], title=row["title"])


def insert_episode(connection: Connection, episode: NewEpisode, now: str) -> None:
    """Adds an active episode. Its sources, links and index row are the caller's to add."""
    connection.execute(
        text(
            "INSERT INTO episodic_memories (id, character_id, conversation_id, title, description, occurred_at, "
            "participants_json, importance, fingerprint, created_at, updated_at) VALUES (:id, :character_id, "
            ":conversation_id, :title, :description, :occurred_at, :participants, :importance, :fingerprint, :now, "
            ":now)"
        ),
        {
            "id": episode.id,
            "character_id": episode.character_id,
            "conversation_id": episode.conversation_id,
            "title": episode.title,
            "description": episode.description,
            "occurred_at": episode.occurred_at,
            "participants": json.dumps(list(episode.participants), ensure_ascii=False),
            "importance": episode.importance,
            "fingerprint": episode.fingerprint,
            "now": now,
        },
    )


def add_episode_sources(connection: Connection, episode_id: str, sources: Sequence[WindowMessage]) -> int:
    """Records each message, with its active variant and content hash, as a source of the episode, and returns how
    many were new."""
    added = 0
    for source in sources:
        result = connection.execute(
            text(
                "INSERT OR IGNORE INTO episodic_sources (episode_id, message_id, variant_id, content_hash) "
                "VALUES (:episode_id, :message_id, :variant_id, :content_hash)"
            ),
            {
                "episode_id": episode_id,
                "message_id": source.message_id,
                "variant_id": source.variant_id,
                "content_hash": source.content_hash,
            },
        )
        added += result.rowcount
    return added


def link_episodes(connection: Connection, from_episode_id: str, to_episode_id: str, relation: str) -> None:
    """Links two different episodes; a link already stored is kept."""
    connection.execute(
        text(
            "INSERT OR IGNORE INTO episode_links (from_episode_id, to_episode_id, relation) VALUES (:a, :b, :relation)"
        ),
        {"a": from_episode_id, "b": to_episode_id, "relation": relation},
    )


def has_active_extraction_job(connection: Connection, conversation_id: str, exclude_job_id: str | None) -> bool:
    """Tells whether a `memory_extraction` job of the conversation is queued or running, other than
    `exclude_job_id`."""
    return (
        connection.execute(
            text(
                "SELECT 1 FROM generation_jobs WHERE kind = 'memory_extraction' AND conversation_id = :id "
                "AND status IN ('queued', 'running') AND (:exclude IS NULL OR id != :exclude) LIMIT 1"
            ),
            {"id": conversation_id, "exclude": exclude_job_id},
        ).first()
        is not None
    )
