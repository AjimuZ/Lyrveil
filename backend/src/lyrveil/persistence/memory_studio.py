"""SQL of the Memory Studio: the filtered list of memories, a memory with its sources, conflict group and history, the
episodes with their sources and links, the summary checkpoints, the relationship state with its change log, and the
small writes of lock, unlock, confirm and a manual memory.

Every function works on a connection the caller owns and never commits. Readers use the READ option of
lyrveil.persistence.database and writers the WRITE option, so a revision is written with the change it records.
Rows come back as plain dicts with the column names, booleans as `bool` and JSON columns decoded.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, RowMapping, bindparam, text

from lyrveil.memory.text import match_query
from lyrveil.persistence.memory_extraction import revise

EXCERPT_CHARS = 200

_MEMORY_COLUMNS = """
m.id, m.character_id, m.scope, m.conversation_id, m.origin_conversation_id, m.kind, m.content, m.subject_key,
m.value_text, m.provenance, m.durability, m.status, m.importance, m.confidence, m.locked, m.needs_review, m.valid_from,
m.valid_to, m.expires_at, m.superseded_by_id, m.conflict_group_id, m.version, m.language, m.last_used_at, m.use_count,
m.created_at, m.updated_at,
(SELECT count(*) FROM memory_sources AS s WHERE s.memory_id = m.id) AS source_count
"""
_EPISODE_COLUMNS = (
    "id, character_id, conversation_id, title, description, occurred_at, participants_json, importance, status, "
    "locked, created_at, updated_at"
)
_SUMMARY_COLUMNS = (
    "id, level, from_seq, to_seq, version, status, text, content_json, prompt_version, token_count, created_at"
)


@dataclass(frozen=True)
class MemoryFilter:
    """The filters of the memory list. `statuses` empty means every status. `ids`, when not None, keeps only those
    memories (the result of a text search; an empty tuple matches nothing). `character_id` is always required."""

    character_id: str
    conversation_id: str | None = None
    scope: str | None = None
    kind: str | None = None
    provenance: str | None = None
    statuses: tuple[str, ...] = ()
    locked: bool | None = None
    ids: tuple[str, ...] | None = None


def _memory(row: RowMapping) -> dict[str, Any]:
    memory = dict(row)
    memory["locked"] = bool(memory["locked"])
    memory["needs_review"] = bool(memory["needs_review"])
    return memory


def conversation_exists(connection: Connection, conversation_id: str) -> bool:
    """Tells whether the conversation exists and is not deleted."""
    found = connection.execute(
        text("SELECT 1 FROM conversations WHERE id = :id AND deleted_at IS NULL"), {"id": conversation_id}
    ).first()
    return found is not None


def character_exists(connection: Connection, character_id: str) -> bool:
    """Tells whether a character with this id exists."""
    return connection.execute(text("SELECT 1 FROM characters WHERE id = :id"), {"id": character_id}).first() is not None


def conversation_owner(connection: Connection, conversation_id: str) -> tuple[str, str] | None:
    """Returns `(character_id, effective language)` of a conversation that is not deleted, or None."""
    row = connection.execute(
        text(
            "SELECT c.character_id, coalesce(c.language, ch.primary_language) FROM conversations AS c "
            "JOIN characters AS ch ON ch.id = c.character_id WHERE c.id = :id AND c.deleted_at IS NULL"
        ),
        {"id": conversation_id},
    ).first()
    return None if row is None else (str(row[0]), str(row[1]))


def search_languages(connection: Connection, character_id: str) -> list[str]:
    """Returns the languages a text search stems with for a character: its memories' languages and its own."""
    rows = connection.execute(
        text(
            "SELECT language FROM memories WHERE character_id = :id UNION "
            "SELECT primary_language FROM characters WHERE id = :id ORDER BY 1"
        ),
        {"id": character_id},
    )
    return [str(language) for (language,) in rows if language]


def matching_episode_ids(connection: Connection, query: str, languages: Sequence[str], limit: int) -> list[str]:
    """Returns the ids of episodes whose index row shares a stem with `query` in any of `languages`."""
    found: list[str] = []
    for language in languages:
        match = match_query(query, language)
        if match is None:
            continue
        rows = connection.execute(
            text("SELECT episode_id FROM episode_fts WHERE episode_fts MATCH :match LIMIT :limit"),
            {"match": match, "limit": limit},
        )
        found.extend(str(episode_id) for (episode_id,) in rows if episode_id not in found)
    return found


def list_memories(
    connection: Connection, filters: MemoryFilter, limit: int, after: tuple[str, str] | None
) -> list[dict[str, Any]]:
    """Returns up to `limit` memories that pass `filters`, the most recently changed first (`updated_at`, then id),
    starting after the `(updated_at, id)` pair `after` when given. Each row carries its `source_count`."""
    clauses = ["m.character_id = :character_id"]
    params: dict[str, Any] = {"character_id": filters.character_id, "limit": limit}
    exact = {
        "conversation_id": filters.conversation_id,
        "scope": filters.scope,
        "kind": filters.kind,
        "provenance": filters.provenance,
    }
    for column, value in exact.items():
        if value is not None:
            clauses.append(f"m.{column} = :{column}")
            params[column] = value
    if filters.locked is not None:
        clauses.append("m.locked = :locked")
        params["locked"] = int(filters.locked)
    if after is not None:
        clauses.append("(m.updated_at < :after_at OR (m.updated_at = :after_at AND m.id < :after_id))")
        params["after_at"], params["after_id"] = after
    statement = f"SELECT {_MEMORY_COLUMNS} FROM memories AS m WHERE "
    expanding: list[str] = []
    if filters.statuses:
        clauses.append("m.status IN :statuses")
        params["statuses"] = list(filters.statuses)
        expanding.append("statuses")
    if filters.ids is not None:
        clauses.append("m.id IN :ids")
        params["ids"] = list(filters.ids) or [""]
        expanding.append("ids")
    query = text(statement + " AND ".join(clauses) + " ORDER BY m.updated_at DESC, m.id DESC LIMIT :limit")
    rows = connection.execute(query.bindparams(*(bindparam(name, expanding=True) for name in expanding)), params)
    return [_memory(row) for row in rows.mappings()]


def get_memory(connection: Connection, memory_id: str) -> dict[str, Any] | None:
    """Returns the memory in any status, or None when no row has that id."""
    row = (
        connection.execute(text(f"SELECT {_MEMORY_COLUMNS} FROM memories AS m WHERE m.id = :id"), {"id": memory_id})
        .mappings()
        .first()
    )
    return None if row is None else _memory(row)


def _source(row: RowMapping, valid: bool) -> dict[str, Any]:
    content = row["content"] or ""
    return {
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "seq": row["seq"],
        "author": row["author"],
        "excerpt": content[:EXCERPT_CHARS],
        "valid": valid,
    }


def memory_sources(connection: Connection, memory_id: str) -> list[dict[str, Any]]:
    """Returns the source messages of a memory in message order. `valid` is false once the message is deleted or its
    active variant is no longer the one the memory read; the excerpt is the start of the variant it read."""
    rows = connection.execute(
        text(
            "SELECT src.message_id, msg.conversation_id, msg.seq, msg.author, v.content, "
            "(msg.deleted_at IS NULL AND msg.active_variant_id = src.variant_id) AS valid "
            "FROM memory_sources AS src JOIN messages AS msg ON msg.id = src.message_id "
            "LEFT JOIN message_variants AS v ON v.id = src.variant_id WHERE src.memory_id = :id "
            "ORDER BY msg.seq, src.message_id"
        ),
        {"id": memory_id},
    ).mappings()
    return [_source(row, bool(row["valid"])) for row in rows]


def conflict_peers(connection: Connection, memory: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Returns the other memories of the memory's conflict group (empty without one), oldest first."""
    if memory["conflict_group_id"] is None:
        return []
    rows = connection.execute(
        text(
            "SELECT id, content, status, provenance, confidence FROM memories "
            "WHERE conflict_group_id = :group AND id != :id ORDER BY created_at, id"
        ),
        {"group": memory["conflict_group_id"], "id": memory["id"]},
    ).mappings()
    return [dict(row) for row in rows]


def memory_revisions(connection: Connection, memory_id: str) -> list[dict[str, Any]]:
    """Returns the history of a memory, newest first: each revision is the row as it was under `version`, with who
    changed it next (`actor`) and why (`reason`)."""
    rows = connection.execute(
        text(
            "SELECT version, snapshot_json, actor, reason, created_at FROM memory_revisions "
            "WHERE memory_id = :id ORDER BY version DESC"
        ),
        {"id": memory_id},
    ).mappings()
    revisions = []
    for row in rows:
        snapshot = json.loads(row["snapshot_json"])
        revisions.append(
            {
                "version": row["version"],
                "actor": row["actor"],
                "reason": row["reason"],
                "created_at": row["created_at"],
                "content": snapshot.get("content"),
                "subject": snapshot.get("subject_key"),
                "value": snapshot.get("value_text"),
                "status": snapshot.get("status"),
                "provenance": snapshot.get("provenance"),
                "confidence": snapshot.get("confidence"),
                "importance": snapshot.get("importance"),
            }
        )
    return revisions


def set_locked(connection: Connection, memory_id: str, locked: bool, now: str) -> None:
    """Sets the lock. A lock keeps the text and the version, so it writes no revision."""
    connection.execute(
        text("UPDATE memories SET locked = :locked, updated_at = :now WHERE id = :id"),
        {"id": memory_id, "locked": int(locked), "now": now},
    )


def confirm(connection: Connection, memory_id: str, now: str) -> None:
    """Records a revision by `user` with reason `confirm` and makes the memory `user_confirmed` with confidence 1.0;
    the review flag is cleared. Its status, text and lock are kept."""
    revise(connection, memory_id, actor="user", reason="confirm", run_id=None, now=now)
    connection.execute(
        text("UPDATE memories SET provenance = 'user_confirmed', confidence = 1.0, needs_review = 0 WHERE id = :id"),
        {"id": memory_id},
    )


def fingerprint_taken(connection: Connection, character_id: str, conversation_id: str, fingerprint: str) -> bool:
    """Tells whether a memory in use (active or uncertain) of the conversation already has this fingerprint."""
    return (
        connection.execute(
            text(
                "SELECT 1 FROM memories WHERE character_id = :character_id AND conversation_id = :conversation_id "
                "AND fingerprint = :fingerprint AND status IN ('active', 'uncertain') LIMIT 1"
            ),
            {"character_id": character_id, "conversation_id": conversation_id, "fingerprint": fingerprint},
        ).first()
        is not None
    )


def list_episodes(
    connection: Connection,
    character_id: str,
    conversation_id: str | None,
    status: str,
    ids: Sequence[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Returns up to `limit` episodes of the character in `status`, the most recent first (`occurred_at`, then id),
    each with its decoded participants, its `sources` and its `links` to other episodes. `ids`, when not None, keeps
    only those episodes."""
    clauses = ["character_id = :character_id", "status = :status"]
    params: dict[str, Any] = {"character_id": character_id, "status": status, "limit": limit}
    if conversation_id is not None:
        clauses.append("conversation_id = :conversation_id")
        params["conversation_id"] = conversation_id
    query = text(
        f"SELECT {_EPISODE_COLUMNS} FROM episodic_memories WHERE "
        + " AND ".join(clauses)
        + (" AND id IN :ids" if ids is not None else "")
        + " ORDER BY occurred_at DESC, id DESC LIMIT :limit"
    )
    if ids is not None:
        params["ids"] = list(ids) or [""]
        query = query.bindparams(bindparam("ids", expanding=True))
    episodes = []
    for row in connection.execute(query, params).mappings():
        episode = dict(row)
        episode["locked"] = bool(episode["locked"])
        episode["participants"] = json.loads(episode.pop("participants_json"))
        episode["sources"] = episode_sources(connection, episode["id"])
        episode["links"] = episode_links(connection, episode["id"])
        episodes.append(episode)
    return episodes


def episode_sources(connection: Connection, episode_id: str) -> list[dict[str, Any]]:
    """Returns the source messages of an episode in message order, shaped as `memory_sources`."""
    rows = connection.execute(
        text(
            "SELECT src.message_id, msg.conversation_id, msg.seq, msg.author, v.content, "
            "(msg.deleted_at IS NULL AND msg.active_variant_id = src.variant_id) AS valid "
            "FROM episodic_sources AS src JOIN messages AS msg ON msg.id = src.message_id "
            "LEFT JOIN message_variants AS v ON v.id = src.variant_id WHERE src.episode_id = :id "
            "ORDER BY msg.seq, src.message_id"
        ),
        {"id": episode_id},
    ).mappings()
    return [_source(row, bool(row["valid"])) for row in rows]


def episode_links(connection: Connection, episode_id: str) -> list[dict[str, Any]]:
    """Returns the links of an episode to active episodes, both ways: `direction` is `from` when this episode is the
    origin of the link and `to` when it is the target, and `title` is the other episode's."""
    rows = connection.execute(
        text(
            "SELECT 'from' AS direction, l.relation, e.id AS episode_id, e.title FROM episode_links AS l "
            "JOIN episodic_memories AS e ON e.id = l.to_episode_id "
            "WHERE l.from_episode_id = :id AND e.status = 'active' "
            "UNION ALL "
            "SELECT 'to' AS direction, l.relation, e.id AS episode_id, e.title FROM episode_links AS l "
            "JOIN episodic_memories AS e ON e.id = l.from_episode_id "
            "WHERE l.to_episode_id = :id AND e.status = 'active' "
            "ORDER BY 1, 2, 4"
        ),
        {"id": episode_id},
    ).mappings()
    return [dict(row) for row in rows]


def conversation_summaries(connection: Connection, conversation_id: str) -> list[dict[str, Any]]:
    """Returns every checkpoint of the conversation in any status, oldest range first, lowest level first, then by
    version, with its prose and lists (`events`, `decisions`, ..., `source_seqs`)."""
    rows = connection.execute(
        text(
            f"SELECT {_SUMMARY_COLUMNS} FROM conversation_summaries WHERE conversation_id = :id "
            "ORDER BY from_seq, level, to_seq, version"
        ),
        {"id": conversation_id},
    ).mappings()
    summaries = []
    for row in rows:
        summary = dict(row)
        summary["content"] = json.loads(summary.pop("content_json"))
        summaries.append(summary)
    return summaries


def relationship_row(connection: Connection, conversation_id: str) -> dict[str, Any] | None:
    """Returns the conversation's `relationship_states` row with its lists decoded and the titles of its recent
    episodes, or None when the state was never written."""
    row = (
        connection.execute(
            text("SELECT * FROM relationship_states WHERE conversation_id = :id"), {"id": conversation_id}
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    state = dict(row)
    for name in ("open_threads", "goals", "narrative_changes"):
        state[name] = json.loads(state.pop(f"{name}_json"))
    ids = json.loads(state.pop("recent_event_ids_json"))
    titles = {}
    if ids:
        rows = connection.execute(
            text("SELECT id, title, occurred_at FROM episodic_memories WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": ids},
        )
        titles = {episode_id: {"id": episode_id, "title": title, "occurred_at": at} for episode_id, title, at in rows}
    state["recent_events"] = [titles[episode_id] for episode_id in ids if episode_id in titles]
    return state


def state_history(connection: Connection, conversation_id: str, limit: int) -> list[dict[str, Any]]:
    """Returns up to `limit` rows of the conversation's change log in any status, the newest first, each with its
    cited messages as `sources` (a message that no longer exists is left out)."""
    rows = connection.execute(
        text(
            "SELECT id, event_kind, dimension, delta, value_before, value_after, reason, source_message_ids_json, "
            "run_id, status, created_at FROM character_state_history WHERE conversation_id = :id "
            "ORDER BY created_at DESC, rowid DESC LIMIT :limit"
        ),
        {"id": conversation_id, "limit": limit},
    ).mappings()
    history = []
    for row in rows:
        entry = dict(row)
        entry["sources"] = message_sources(connection, json.loads(entry.pop("source_message_ids_json")))
        history.append(entry)
    return history


def message_sources(connection: Connection, message_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Returns the messages `message_ids` that still exist, in message order, with the start of their active variant;
    `valid` is false for a deleted message."""
    if not message_ids:
        return []
    rows = connection.execute(
        text(
            "SELECT msg.id AS message_id, msg.conversation_id, msg.seq, msg.author, v.content, "
            "(msg.deleted_at IS NULL) AS valid FROM messages AS msg "
            "LEFT JOIN message_variants AS v ON v.id = msg.active_variant_id WHERE msg.id IN :ids ORDER BY msg.seq"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": list(message_ids)},
    ).mappings()
    return [_source(row, bool(row["valid"])) for row in rows]
