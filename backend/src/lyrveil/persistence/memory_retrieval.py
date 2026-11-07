"""SQL of memory retrieval: the candidates of one query under the hard scope filters, and the use counters.

Every read applies the filters of docs/memory-engine.md (Candidates) in SQL, before any ranking: the character, the
conversation or (when shared memory is on for it) the shared scope, the statuses a prompt may use, no expiry passed and
no identity candidate. So no row of another character or another conversation ever reaches the ranking code.
Functions work on a connection the caller owns and never commit.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, RowMapping, bindparam, text

from lyrveil.domain.identifiers import from_utc_text
from lyrveil.memory.retrieval import EPISODE_KIND, IN_USE_STATUSES, PAST_STATUS, Candidate


@dataclass(frozen=True)
class Scope:
    """Whose memories a query may read. `include_shared` adds the character's shared scope, and only takes effect when
    the conversation belongs to the character and has `shares_memory = 1`. `past_intent` adds superseded memories.
    `now` is UTC text (to_utc_text): a memory whose `expires_at` is not after it is left out."""

    character_id: str
    conversation_id: str
    include_shared: bool
    past_intent: bool
    now: str


_MEMORY_COLUMNS = """
m.id, m.kind, m.content, m.subject_key, m.provenance, m.durability, m.status, m.importance, m.confidence, m.locked,
m.conflict_group_id, m.language, m.updated_at
"""
# The shared scope needs both the caller's flag and the conversation's own opt-in, so a wrong flag alone leaks nothing.
_MEMORY_FILTER = """
m.character_id = :character_id
AND (m.conversation_id = :conversation_id OR (:include_shared = 1 AND m.scope = 'shared' AND EXISTS (
    SELECT 1 FROM conversations AS c WHERE c.id = :conversation_id AND c.character_id = :character_id
    AND c.shares_memory = 1)))
AND m.status IN :statuses
AND (m.expires_at IS NULL OR m.expires_at > :now)
AND m.kind <> 'identity_candidate'
"""
# Standing memories apply whatever words the user chose (docs/memory-engine.md, Candidates).
_STANDING = (
    "(m.locked = 1 OR m.importance = 5 OR m.kind = 'boundary' "
    "OR (m.status = 'active' AND m.kind IN ('promise', 'open_thread')))"
)
# BM25 weights per column: none for the unindexed identifier, then the stemmed subject or title twice the text.
_MATCHING_MEMORIES = (
    f"SELECT {_MEMORY_COLUMNS}, -bm25(memory_fts, 0.0, 2.0, 1.0) AS relevance FROM memory_fts "
    f"JOIN memories AS m ON m.id = memory_fts.memory_id WHERE memory_fts MATCH :match AND {_MEMORY_FILTER} "
    "ORDER BY bm25(memory_fts, 0.0, 2.0, 1.0), m.id LIMIT :limit"
)
_STANDING_MEMORIES = (
    f"SELECT {_MEMORY_COLUMNS} FROM memories AS m WHERE {_MEMORY_FILTER} AND {_STANDING} "
    "ORDER BY m.locked DESC, m.importance DESC, m.updated_at DESC, m.id LIMIT :limit"
)
_CONFLICT_MEMBERS = (
    f"SELECT {_MEMORY_COLUMNS} FROM memories AS m WHERE {_MEMORY_FILTER} AND m.status = 'uncertain' "
    "AND m.conflict_group_id IN :groups ORDER BY m.id"
)
_MATCHING_EPISODES = (
    "SELECT e.id, e.title, e.description, e.importance, e.locked, e.updated_at, "
    "-bm25(episode_fts, 0.0, 2.0, 1.0) AS relevance FROM episode_fts "
    "JOIN episodic_memories AS e ON e.id = episode_fts.episode_id "
    "WHERE episode_fts MATCH :match AND e.character_id = :character_id "
    "AND e.conversation_id = :conversation_id AND e.status = 'active' "
    "ORDER BY bm25(episode_fts, 0.0, 2.0, 1.0), e.id LIMIT :limit"
)
_RECORD_USE = "UPDATE memories SET last_used_at = :now, use_count = use_count + 1 WHERE id IN :ids"


def _statuses(scope: Scope) -> list[str]:
    return [*IN_USE_STATUSES, PAST_STATUS] if scope.past_intent else list(IN_USE_STATUSES)


def _params(scope: Scope) -> dict[str, object]:
    return {
        "character_id": scope.character_id,
        "conversation_id": scope.conversation_id,
        "include_shared": int(scope.include_shared),
        "statuses": _statuses(scope),
        "now": scope.now,
    }


def _memory(row: RowMapping, bm25: float | None) -> Candidate:
    return Candidate(
        id=row["id"],
        item_type="memory",
        kind=row["kind"],
        content=row["content"],
        subject_key=row["subject_key"],
        provenance=row["provenance"],
        durability=row["durability"],
        status=row["status"],
        importance=row["importance"],
        confidence=float(row["confidence"]),
        locked=bool(row["locked"]),
        conflict_group_id=row["conflict_group_id"],
        language=row["language"],
        updated_at=from_utc_text(row["updated_at"]),
        bm25=bm25,
    )


def matching_memories(connection: Connection, scope: Scope, match: str, limit: int) -> list[Candidate]:
    """Returns up to `limit` memories in scope whose index row matches `match` (lyrveil.memory.text.match_query), best
    BM25 first, each with `bm25` set to `-bm25(memory_fts, 0.0, 2.0, 1.0)`, so higher is better."""
    statement = text(_MATCHING_MEMORIES).bindparams(bindparam("statuses", expanding=True))
    rows = connection.execute(statement, {**_params(scope), "match": match, "limit": limit}).mappings().all()
    return [_memory(row, float(row["relevance"])) for row in rows]


def standing_memories(connection: Connection, scope: Scope, limit: int) -> list[Candidate]:
    """Returns up to `limit` standing memories in scope (locked, importance 5, `boundary`, or an active `promise` or
    `open_thread`), locked first, then the most important and most recently changed, with no BM25."""
    statement = text(_STANDING_MEMORIES).bindparams(bindparam("statuses", expanding=True))
    rows = connection.execute(statement, {**_params(scope), "limit": limit}).mappings().all()
    return [_memory(row, None) for row in rows]


def conflict_members(connection: Connection, scope: Scope, group_ids: Sequence[str]) -> list[Candidate]:
    """Returns the uncertain memories in scope of these conflict groups, with no BM25, so that a group the query
    reached through one member can be shown whole. An empty `group_ids` reads nothing."""
    if not group_ids:
        return []
    statement = text(_CONFLICT_MEMBERS).bindparams(
        bindparam("statuses", expanding=True), bindparam("groups", expanding=True)
    )
    rows = connection.execute(statement, {**_params(scope), "groups": list(group_ids)}).mappings().all()
    return [_memory(row, None) for row in rows]


def matching_episodes(connection: Connection, scope: Scope, match: str, limit: int, language: str) -> list[Candidate]:
    """Returns up to `limit` active episodes of the character's conversation whose index row matches `match`, best
    BM25 first, with `bm25` set to `-bm25(episode_fts, 0.0, 2.0, 1.0)`.

    Episodes have no shared scope and store no language, provenance or confidence: their content is
    `title: description`, their language is `language` (the conversation's, which extraction indexed them with), and
    they count as stated, lasting items of confidence 1.0.
    """
    params = {
        "character_id": scope.character_id,
        "conversation_id": scope.conversation_id,
        "match": match,
        "limit": limit,
    }
    rows = connection.execute(text(_MATCHING_EPISODES), params).mappings().all()
    return [
        Candidate(
            id=row["id"],
            item_type="episode",
            kind=EPISODE_KIND,
            content=f"{row['title']}: {row['description']}",
            subject_key=None,
            provenance="stated",
            durability="lasting",
            status="active",
            importance=row["importance"],
            confidence=1.0,
            locked=bool(row["locked"]),
            conflict_group_id=None,
            language=language,
            updated_at=from_utc_text(row["updated_at"]),
            bm25=float(row["relevance"]),
        )
        for row in rows
    ]


def record_use(connection: Connection, memory_ids: Sequence[str], now: str) -> int:
    """Sets `last_used_at` to `now` and adds 1 to `use_count` of these memories, and returns how many rows changed.
    Identifiers of episodes or of deleted memories are ignored. `updated_at` is left alone, so being used does not
    count as being recent."""
    if not memory_ids:
        return 0
    statement = text(_RECORD_USE).bindparams(bindparam("ids", expanding=True))
    return connection.execute(statement, {"now": now, "ids": list(memory_ids)}).rowcount
