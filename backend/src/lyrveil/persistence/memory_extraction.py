"""SQL of memory extraction: the messages of a window, the cursor, the runs, the memories and episodes a run shows and
writes, and the conversation's extraction jobs.

Every function works on a connection the caller owns and never commits. Writers open that connection with the WRITE
option of lyrveil.persistence.database, so the service decides what one transaction holds: a run's memories, sources,
index rows, episodes, its own row and the cursor are written together or not at all.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.messages import Author, MessageStatus
from lyrveil.memory.window import Window, WindowMessage

_WINDOW_SELECT = """
SELECT m.id, m.seq, m.author, m.status, m.deleted_at, m.created_at, v.id AS variant_id, v.content, v.content_hash
FROM messages AS m LEFT JOIN message_variants AS v ON v.id = m.active_variant_id
"""
# The memories a run may refer to: those of the conversation that prompts use.
_SHOWN_STATUSES = "('active', 'uncertain')"


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
    """An existing memory a run shows to the model (as `E1`...) or finds by fingerprint."""

    id: str
    content: str
    importance: int
    locked: bool


@dataclass(frozen=True)
class ShownEpisode:
    """A recent episode a run shows to the model (as `P1`...) or finds by fingerprint."""

    id: str
    title: str


@dataclass(frozen=True)
class NewMemory:
    """The columns of a conversation memory that extraction inserts. Status is `active` and version 1."""

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
    return ShownMemory(id=row["id"], content=row["content"], importance=row["importance"], locked=bool(row["locked"]))


def related_memories(connection: Connection, conversation_id: str, match: str, limit: int) -> list[ShownMemory]:
    """Returns up to `limit` active or uncertain memories of the conversation whose index row matches `match` (from
    lyrveil.memory.text.match_query), best match first."""
    rows = (
        connection.execute(
            text(
                "SELECT m.id, m.content, m.importance, m.locked FROM memory_fts JOIN memories AS m "
                "ON m.id = memory_fts.memory_id WHERE memory_fts MATCH :match AND m.conversation_id = :id "
                f"AND m.status IN {_SHOWN_STATUSES} ORDER BY bm25(memory_fts, 0.0, 2.0, 1.0), m.id LIMIT :limit"
            ),
            {"match": match, "id": conversation_id, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_shown_memory(row) for row in rows]


def recent_memories(connection: Connection, conversation_id: str, limit: int) -> list[ShownMemory]:
    """Returns up to `limit` active or uncertain memories of the conversation, the most recently changed first."""
    rows = (
        connection.execute(
            text(
                "SELECT id, content, importance, locked FROM memories WHERE conversation_id = :id "
                f"AND status IN {_SHOWN_STATUSES} ORDER BY updated_at DESC, id DESC LIMIT :limit"
            ),
            {"id": conversation_id, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_shown_memory(row) for row in rows]


def find_memory_by_fingerprint(
    connection: Connection, character_id: str, conversation_id: str, fingerprint: str
) -> ShownMemory | None:
    """Returns the active or uncertain memory of the conversation with this fingerprint, the one the unique index
    allows, or None."""
    row = (
        connection.execute(
            text(
                "SELECT id, content, importance, locked FROM memories WHERE character_id = :character_id "
                f"AND conversation_id = :id AND fingerprint = :fingerprint AND status IN {_SHOWN_STATUSES}"
            ),
            {"character_id": character_id, "id": conversation_id, "fingerprint": fingerprint},
        )
        .mappings()
        .first()
    )
    return None if row is None else _shown_memory(row)


def insert_memory(connection: Connection, memory: NewMemory, now: str) -> None:
    """Adds an active conversation memory at version 1. Its sources and index row are the caller's to add."""
    connection.execute(
        text(
            "INSERT INTO memories (id, character_id, scope, conversation_id, kind, content, subject_key, value_text, "
            "provenance, durability, status, importance, confidence, valid_from, expires_at, fingerprint, language, "
            "created_at, updated_at) VALUES (:id, :character_id, 'conversation', :conversation_id, :kind, :content, "
            ":subject_key, :value_text, :provenance, :durability, 'active', :importance, :confidence, :valid_from, "
            ":expires_at, :fingerprint, :language, :now, :now)"
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
            "importance": memory.importance,
            "confidence": memory.confidence,
            "valid_from": memory.valid_from,
            "expires_at": memory.expires_at,
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
