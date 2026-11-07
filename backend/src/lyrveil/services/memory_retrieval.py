"""FtsMemoryRetriever: the 1.0 MemoryRetriever, FTS5 candidates with Snowball stems, scored and packed into a token
budget (docs/memory-engine.md, Retrieval; ADR 0011).

Candidates are read in one read transaction under the hard scope filters of lyrveil.persistence.memory_retrieval; the
scoring and selection are the pure rules of lyrveil.memory.retrieval. Nothing is written by a retrieval: the use
counters are recorded separately, after the reply is committed.
"""

from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy import Connection, Engine

from lyrveil.domain.identifiers import to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.memory.retrieval import (
    MAX_EPISODE_CANDIDATES,
    MAX_FTS_CANDIDATES,
    MAX_STANDING_CANDIDATES,
    Candidate,
    RetrievalWeights,
    estimate_tokens,
    score_all,
    select,
)
from lyrveil.memory.text import match_query, stems
from lyrveil.persistence import memory_retrieval as repository
from lyrveil.persistence.memory_retrieval import Scope
from lyrveil.ports.memory_retriever import RetrievalQuery, RetrievalResult

# The query is the last three turns; past this many distinct stems the rest adds little and slows the MATCH.
MAX_QUERY_TERMS = 48


class FtsMemoryRetriever:
    """Retrieves from the database of `engine`. `weights` are the `memory.retrieval.*` settings; `token_cost` prices
    one rendered prompt line, by default lyrveil.memory.retrieval.estimate_tokens, and must never raise. Raises
    ValueError at construction when the weights cannot be normalized."""

    def __init__(
        self,
        engine: Engine,
        *,
        weights: RetrievalWeights | None = None,
        token_cost: Callable[[str], int] = estimate_tokens,
    ) -> None:
        self._engine = engine
        self._weights = weights or RetrievalWeights()
        # Fails at construction, not on the first reply, when the weights cannot be normalized.
        self._weights.normalized()
        self._token_cost = token_cost

    def retrieve_relevant_memories(self, query: RetrievalQuery) -> RetrievalResult:
        """Return the best memories and episodes that fit `token_budget`.

        Results always belong to `character_id`, and to `conversation_id` or, when `include_shared` is true and the
        conversation has `shares_memory = 1`, to the shared scope. Candidates are up to 64 FTS matches of the query's
        stems, up to 16 standing memories, the other members of any conflict group they reach, and up to 16 matching
        episodes. Each item holds its score parts. An empty result is valid. Raises only on a database failure, and
        ValueError for a naive `now`.
        """
        scope = Scope(
            character_id=query.character_id,
            conversation_id=query.conversation_id,
            include_shared=query.include_shared,
            past_intent=query.past_intent,
            now=to_utc_text(query.now),
        )
        match = match_query(query.text, query.language, max_terms=MAX_QUERY_TERMS)

        def work(connection: Connection) -> list[Candidate]:
            found: dict[str, Candidate] = {}
            if match is not None:
                for candidate in repository.matching_memories(connection, scope, match, MAX_FTS_CANDIDATES):
                    found[candidate.id] = candidate
            for candidate in repository.standing_memories(connection, scope, MAX_STANDING_CANDIDATES):
                found.setdefault(candidate.id, candidate)
            groups = sorted(
                {
                    candidate.conflict_group_id
                    for candidate in found.values()
                    if candidate.conflict_group_id is not None and candidate.status == "uncertain"
                }
            )
            for candidate in repository.conflict_members(connection, scope, groups):
                found.setdefault(candidate.id, candidate)
            if match is not None:
                episodes = repository.matching_episodes(
                    connection, scope, match, MAX_EPISODE_CANDIDATES, query.language
                )
                for candidate in episodes:
                    found[candidate.id] = candidate
            return list(found.values())

        candidates = read(self._engine, work)
        scored = score_all(
            candidates,
            topic_stems=frozenset(stems(query.topic or "", query.language)),
            now=query.now,
            previous_ids=query.previous_ids,
            weights=self._weights,
        )
        items = select(scored, token_budget=query.token_budget, max_items=query.max_items, token_cost=self._token_cost)
        return RetrievalResult(
            items=tuple(items), token_cost=sum(item.token_cost for item in items), candidate_count=len(candidates)
        )

    def record_use(self, memory_ids: Sequence[str], now: datetime) -> int:
        """Step 5 of Selection, to call once the reply that used the selection is committed: sets `last_used_at` and
        adds 1 to `use_count` of each memory, in one write transaction, and returns how many memories changed. Episode
        identifiers are ignored. Raises ValueError for a naive `now`."""
        moment = to_utc_text(now)
        distinct = sorted(set(memory_ids))
        return write(self._engine, lambda connection: repository.record_use(connection, distinct, moment))
