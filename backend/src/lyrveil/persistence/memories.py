"""SQL of the user's own changes to a memory: reading it, editing it with a revision, and deleting it with a tombstone.

Every function works on a connection the caller owns and never commits; the service opens it with the WRITE option of
lyrveil.persistence.database, so an edit or a deletion is one transaction with its revision or tombstone.
"""

import json
from dataclasses import dataclass

from sqlalchemy import Connection, text

from lyrveil.domain.identifiers import new_id
from lyrveil.memory.consistency import Tombstone
from lyrveil.persistence.memory_extraction import revise


@dataclass(frozen=True)
class StoredMemory:
    """The columns of a memory a user edit reads and returns. `conversation_id` is None for a shared memory."""

    id: str
    character_id: str
    conversation_id: str | None
    content: str
    subject_key: str | None
    value_text: str | None
    importance: int
    status: str
    version: int
    fingerprint: str
    language: str


def get(connection: Connection, memory_id: str) -> StoredMemory | None:
    """Returns the memory in any status, or None when no row has that id."""
    row = (
        connection.execute(
            text(
                "SELECT id, character_id, conversation_id, content, subject_key, value_text, importance, status, "
                "version, fingerprint, language FROM memories WHERE id = :id"
            ),
            {"id": memory_id},
        )
        .mappings()
        .first()
    )
    return None if row is None else StoredMemory(**row)


def fingerprint_in_use(connection: Connection, memory: StoredMemory, fingerprint: str) -> bool:
    """Tells whether another memory of the same owner (a conversation, or the shared scope) is in use with this
    fingerprint, which the unique index would refuse."""
    return (
        connection.execute(
            text(
                "SELECT 1 FROM memories WHERE character_id = :character_id "
                "AND ifnull(conversation_id, 'shared') = ifnull(:conversation_id, 'shared') "
                "AND fingerprint = :fingerprint AND status IN ('active', 'uncertain') AND id != :id LIMIT 1"
            ),
            {
                "character_id": memory.character_id,
                "conversation_id": memory.conversation_id,
                "fingerprint": fingerprint,
                "id": memory.id,
            },
        ).first()
        is not None
    )


def edit(
    connection: Connection,
    memory: StoredMemory,
    *,
    content: str,
    subject_key: str | None,
    value_text: str | None,
    importance: int,
    fingerprint: str,
    now: str,
) -> None:
    """Records a revision by `user` with reason `edit` (the whole row as it was, under its version) and writes the new
    text, subject, value, importance and fingerprint; the memory moves to the next version. Its status, provenance,
    sources and lock are kept. The caller refreshes the index row in the same transaction."""
    revise(connection, memory.id, actor="user", reason="edit", run_id=None, now=now)
    connection.execute(
        text(
            "UPDATE memories SET content = :content, subject_key = :subject_key, value_text = :value_text, "
            "importance = :importance, fingerprint = :fingerprint WHERE id = :id"
        ),
        {
            "id": memory.id,
            "content": content,
            "subject_key": subject_key,
            "value_text": value_text,
            "importance": importance,
            "fingerprint": fingerprint,
        },
    )


def source_facts(connection: Connection, memory_id: str) -> tuple[int | None, list[str]]:
    """Returns the seq of the newest source message of the memory (None when it has no source) and the distinct
    content hashes of its sources, sorted."""
    rows = connection.execute(
        text(
            "SELECT s.content_hash, msg.seq FROM memory_sources AS s JOIN messages AS msg ON msg.id = s.message_id "
            "WHERE s.memory_id = :id"
        ),
        {"id": memory_id},
    ).all()
    newest = max((seq for _, seq in rows), default=None)
    return newest, sorted({content_hash for content_hash, _ in rows})


def insert_tombstone(connection: Connection, memory: StoredMemory, tombstone: Tombstone, now: str) -> None:
    """Adds the tombstone of `memory`: its character and conversation, and the hashes and keys of `tombstone`."""
    connection.execute(
        text(
            "INSERT INTO memory_tombstones (id, character_id, conversation_id, fingerprint, subject_key, value_hash, "
            "source_hashes_json, source_max_seq, created_at) VALUES (:id, :character_id, :conversation_id, "
            ":fingerprint, :subject_key, :value_hash, :source_hashes, :source_max_seq, :now)"
        ),
        {
            "id": new_id(),
            "character_id": memory.character_id,
            "conversation_id": memory.conversation_id,
            "fingerprint": tombstone.fingerprint,
            "subject_key": tombstone.subject_key,
            "value_hash": tombstone.value_hash,
            "source_hashes": json.dumps(sorted(tombstone.source_hashes)),
            "source_max_seq": tombstone.source_max_seq,
            "now": now,
        },
    )


def delete(connection: Connection, memory_id: str) -> None:
    """Removes the memory row, which takes its revisions and sources with it (their keys cascade), and its index row.
    A memory it superseded keeps its row and loses the `superseded_by_id` link (SET NULL). The caller adds the
    tombstone first, in the same transaction."""
    connection.execute(text("DELETE FROM memory_fts WHERE memory_id = :id"), {"id": memory_id})
    connection.execute(text("DELETE FROM memories WHERE id = :id"), {"id": memory_id})
