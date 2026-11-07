"""SQL of `prompt_assemblies`: the diagnostic record of each reply (docs/data-model.md, `prompt_assemblies`).

Every function works on a connection the caller owns and never commits. Rows come back as plain dicts with the column
names and the JSON columns decoded. The retention of docs/security.md applies on every insert: only the newest
RETENTION rows of a conversation are kept.
"""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, RowMapping, bindparam, text

# docs/data-model.md: the newest 200 assemblies of each conversation.
RETENTION = 200
# How much of a reply the list of assemblies shows, to tell the replies apart.
EXCERPT_CHARS = 80


@dataclass(frozen=True)
class NewAssembly:
    """The columns of an assembly that a reply writes. `blocks`, `memory_refs` and `summary_ids` are stored as JSON.
    `prompt_text` is None unless diagnostic mode was on."""

    id: str
    conversation_id: str
    message_id: str
    variant_id: str
    blocks: dict[str, Any]
    memory_refs: list[dict[str, Any]]
    summary_ids: list[str]
    token_total: int
    context_limit: int
    prompt_text: str | None


def insert(connection: Connection, new: NewAssembly, now: str) -> None:
    """Stores the assembly, points the reply's variant at it, and drops the conversation's rows beyond RETENTION."""
    connection.execute(
        text(
            "INSERT INTO prompt_assemblies (id, conversation_id, message_id, variant_id, blocks_json, "
            "memory_refs_json, summary_ids_json, token_total, context_limit, prompt_text, created_at) "
            "VALUES (:id, :conversation_id, :message_id, :variant_id, :blocks, :memory_refs, :summary_ids, "
            ":token_total, :context_limit, :prompt_text, :now)"
        ),
        {
            "id": new.id,
            "conversation_id": new.conversation_id,
            "message_id": new.message_id,
            "variant_id": new.variant_id,
            "blocks": json.dumps(new.blocks, ensure_ascii=False),
            "memory_refs": json.dumps(new.memory_refs, ensure_ascii=False),
            "summary_ids": json.dumps(new.summary_ids),
            "token_total": new.token_total,
            "context_limit": new.context_limit,
            "prompt_text": new.prompt_text,
            "now": now,
        },
    )
    connection.execute(
        text("UPDATE message_variants SET prompt_assembly_id = :id WHERE id = :variant_id"),
        {"id": new.id, "variant_id": new.variant_id},
    )
    _prune(connection, new.conversation_id)


def _prune(connection: Connection, conversation_id: str) -> None:
    stale = (
        "SELECT id FROM prompt_assemblies WHERE conversation_id = :conversation_id "
        "ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET :keep"
    )
    params = {"conversation_id": conversation_id, "keep": RETENTION}
    unlink = f"UPDATE message_variants SET prompt_assembly_id = NULL WHERE prompt_assembly_id IN ({stale})"
    connection.execute(text(unlink), params)
    connection.execute(text(f"DELETE FROM prompt_assemblies WHERE id IN ({stale})"), params)


def _decoded(row: RowMapping) -> dict[str, Any]:
    assembly = dict(row)
    assembly["blocks"] = json.loads(assembly.pop("blocks_json"))
    assembly["memory_refs"] = json.loads(assembly.pop("memory_refs_json"))
    assembly["summary_ids"] = json.loads(assembly.pop("summary_ids_json"))
    return assembly


def for_message(connection: Connection, message_id: str) -> dict[str, Any] | None:
    """Returns the assembly of the message's reply, or None when none was stored.

    It is the one the message's active variant points at (`message_variants.prompt_assembly_id`); a message whose active
    variant has none (an older variant was activated) answers with the message's newest assembly instead.
    """
    row = (
        connection.execute(
            text(
                "SELECT a.id, a.conversation_id, a.message_id, a.variant_id, a.blocks_json, a.memory_refs_json, "
                "a.summary_ids_json, a.token_total, a.context_limit, a.prompt_text, a.created_at "
                "FROM prompt_assemblies AS a JOIN messages AS m ON m.id = a.message_id "
                "LEFT JOIN message_variants AS v ON v.id = m.active_variant_id "
                "WHERE a.message_id = :id "
                "ORDER BY (a.id = v.prompt_assembly_id) DESC, a.created_at DESC, a.id DESC LIMIT 1"
            ),
            {"id": message_id},
        )
        .mappings()
        .first()
    )
    return None if row is None else _decoded(row)


def for_conversation(connection: Connection, conversation_id: str) -> list[dict[str, Any]]:
    """Returns the conversation's stored assemblies, newest first, as the choice list of the diagnostic view: the
    message and its `seq`, the start of the reply's text, the token count and the window. Messages that were deleted are
    left out."""
    rows = connection.execute(
        text(
            "SELECT a.message_id, m.seq, substr(m.content, 1, :chars) AS excerpt, a.token_total, a.context_limit, "
            "a.created_at FROM prompt_assemblies AS a JOIN messages AS m ON m.id = a.message_id "
            "JOIN message_variants AS v ON v.id = m.active_variant_id AND v.prompt_assembly_id = a.id "
            "WHERE a.conversation_id = :conversation_id AND m.deleted_at IS NULL ORDER BY m.seq DESC"
        ),
        {"conversation_id": conversation_id, "chars": EXCERPT_CHARS},
    ).mappings()
    return [dict(row) for row in rows]


def memory_lines(connection: Connection, ids: list[str]) -> dict[str, str]:
    """Returns the current text of the memories and episodes among `ids`, by id. An id with no row (deleted since the
    reply) is absent."""
    found: dict[str, str] = {}
    if not ids:
        return found
    for sql in (
        "SELECT id, content AS line FROM memories WHERE id IN :ids",
        "SELECT id, title AS line FROM episodic_memories WHERE id IN :ids",
    ):
        statement = text(sql).bindparams(bindparam("ids", expanding=True))
        for row in connection.execute(statement, {"ids": ids}):
            found[row[0]] = row[1]
    return found


def summary_rows(connection: Connection, ids: list[str]) -> list[dict[str, Any]]:
    """Returns the checkpoints among `ids`, oldest range first, with their level, range, version, status and size."""
    if not ids:
        return []
    statement = text(
        "SELECT id, level, from_seq, to_seq, version, status, token_count FROM conversation_summaries "
        "WHERE id IN :ids ORDER BY from_seq, level, version"
    ).bindparams(bindparam("ids", expanding=True))
    return [dict(row) for row in connection.execute(statement, {"ids": ids}).mappings()]
