"""Writes and queries the stemmed full-text rows of memories and episodes (`memory_fts`, `episode_fts`)."""

from sqlalchemy import Connection, text

from lyrveil.memory.text import index_text, match_query


def index_memory(
    connection: Connection, memory_id: str, content: str, subject_key: str | None, language: str | None
) -> None:
    """Replaces the index row of a memory with the stems of its text and subject key.

    Call it in the transaction that inserts or changes the memory, so the index never disagrees with the table. The
    subject key is stemmed too: `user.employer` is indexed as the words `user` and `employer`.
    """
    unindex_memory(connection, memory_id)
    stems = {"content": index_text(content, language), "subject": index_text(subject_key or "", language)}
    connection.execute(
        text("INSERT INTO memory_fts (memory_id, content_stem, subject_stem) VALUES (:id, :content, :subject)"),
        {"id": memory_id, **stems},
    )


def unindex_memory(connection: Connection, memory_id: str) -> None:
    """Removes the index row of a memory; does nothing when it has none."""
    connection.execute(text("DELETE FROM memory_fts WHERE memory_id = :id"), {"id": memory_id})


def index_episode(connection: Connection, episode_id: str, title: str, description: str, language: str | None) -> None:
    """Replaces the index row of an episode with the stems of its title and description."""
    unindex_episode(connection, episode_id)
    connection.execute(
        text("INSERT INTO episode_fts (episode_id, title_stem, description_stem) VALUES (:id, :title, :description)"),
        {"id": episode_id, "title": index_text(title, language), "description": index_text(description, language)},
    )


def unindex_episode(connection: Connection, episode_id: str) -> None:
    """Removes the index row of an episode; does nothing when it has none."""
    connection.execute(text("DELETE FROM episode_fts WHERE episode_id = :id"), {"id": episode_id})


def match_memory_ids(connection: Connection, query_text: str, language: str | None, limit: int) -> list[str]:
    """Returns up to `limit` memory identifiers whose indexed text shares a stem with `query_text`, best match first.

    This only consults the index: it does not apply scope, status or expiry filters, which the retriever adds with
    SQL on `memories`. Text without a searchable word gives an empty list.
    """
    match = match_query(query_text, language)
    if match is None:
        return []
    rows = connection.execute(
        text(
            "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH :match "
            "ORDER BY bm25(memory_fts, 0.0, 2.0, 1.0), memory_id LIMIT :limit"
        ),
        {"match": match, "limit": limit},
    )
    return [memory_id for (memory_id,) in rows]
