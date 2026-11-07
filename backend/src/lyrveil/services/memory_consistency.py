"""MemoryConsistencyService: applies the consistency rules (lyrveil.memory.consistency) to the memory items of an
extraction run, and expires temporary memories (docs/memory-engine.md, Consistency rules).

Every method works in the caller's write transaction and never commits, so a run's changes are written with its run
row and cursor or not at all. A change to an existing memory always appends its earlier row to `memory_revisions`
first and keeps its sources; only a duplicate changes a memory without a new version. A candidate that matches a
tombstone of a deleted memory is not stored unless one of its sources is newer than the deleted memory's. A candidate
with the fingerprint of an `invalidated` memory brings that row back, with a revision and its new sources, instead of
adding another (docs/memory-engine.md, Invalidation after message changes, step 7).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection

from lyrveil.domain.identifiers import from_utc_text, new_id, to_utc_text
from lyrveil.domain.memory_extraction import ExtractedMemory, stored_confidence
from lyrveil.domain.messages import Author
from lyrveil.memory import consistency
from lyrveil.memory.consistency import Decision, KnownMemory, Proposal, SourceRef
from lyrveil.memory.text import fingerprint, match_query
from lyrveil.memory.window import WindowMessage, is_significant, phrase_patterns
from lyrveil.persistence import memory_extraction as repository
from lyrveil.persistence.memory_index import index_memory

_IN_USE = frozenset({"active", "uncertain"})
# The near duplicates read per item, best FTS match first, and the stems of its text that look them up.
MAX_SIMILAR_CANDIDATES = 16
MAX_SIMILAR_TERMS = 32
# docs/memory-engine.md (Consistency rules): a conflict of this importance or more fires `memory.conflict`.
CONFLICT_EVENT_IMPORTANCE = 3


@dataclass(frozen=True)
class RunScope:
    """The run whose items are applied: its conversation, character and language, and the time of its commit.
    `reevaluation` is true for a `memory_reevaluation` run, which reads messages the cursor has already passed: a
    temporary memory then lasts from its newest source message, not from the run."""

    run_id: str
    character_id: str
    conversation_id: str
    language: str
    now: datetime
    reevaluation: bool = False


@dataclass(frozen=True)
class Applied:
    """What one item did. `memory_id` is the memory inserted or brought back (`added`; `reactivated` when it was an
    invalidated row) or confirmed. `superseded` and `uncertain` count the existing memories it superseded or made
    uncertain. `conflict` is true when the memory is stored `uncertain` with a conflict importance of at least
    CONFLICT_EVENT_IMPORTANCE, which needs the user's review."""

    memory_id: str
    added: bool
    sources_added: int
    superseded: int = 0
    uncertain: int = 0
    conflict: bool = False
    reactivated: bool = False


@dataclass(frozen=True)
class Blocked:
    """An item a tombstone stopped: it matched a deleted memory and none of its sources is new."""


class MemoryConsistencyService:
    """The deterministic half of extraction: the model proposes items and this service decides what is stored."""

    def apply(
        self,
        connection: Connection,
        scope: RunScope,
        item: ExtractedMemory,
        target_id: str | None,
        sources: Sequence[WindowMessage],
    ) -> Applied | Blocked | None:
        """Applies one valid item and returns what it did, or None when its target is no longer in use (an earlier
        item of the same run superseded or expired it), or Blocked when a tombstone of a memory the user deleted stops
        it (guard 6); both write nothing. A tombstone only stops an item that would store a new memory: one that
        repeats a memory in use is a duplicate, and adds its sources as usual.

        Requires: `sources` are the item's window messages, at least one, in seq order; `target_id` is the memory its
        `target_ref` names, or None; `item.content` is not blank.
        """
        target = None
        if target_id is not None:
            target = repository.known_memory(connection, target_id)
            if target is None or target.status not in _IN_USE:
                return None
        proposal = _proposal(item, sources, scope.language)
        same = repository.known_by_fingerprint(
            connection, scope.character_id, scope.conversation_id, proposal.fingerprint
        )
        match = match_query(proposal.content, scope.language, max_terms=MAX_SIMILAR_TERMS)
        similar = (
            []
            if match is None
            else repository.similar_candidates(
                connection, scope.conversation_id, proposal.kind, proposal.subject_key, match, MAX_SIMILAR_CANDIDATES
            )
        )
        rivals = (
            repository.subject_rivals(connection, scope.conversation_id, proposal.subject_key)
            if proposal.subject_key in consistency.SINGLE_VALUED_SUBJECTS
            else []
        )
        decision = consistency.decide(proposal, target, same, similar, rivals)
        if decision.confirm is not None:
            return _confirm(connection, decision.confirm, proposal.importance, sources, to_utc_text(scope.now))
        if _blocked(connection, scope, proposal, sources):
            return Blocked()
        return _insert(connection, scope, item, proposal, decision, sources)

    def expire(self, connection: Connection, conversation_id: str, now: datetime) -> list[str]:
        """Marks `expired` the temporary memories of the conversation past their `expires_at`, each with a revision,
        and returns their ids. Lasting memories are never touched, so the fact a temporary one sat next to stays."""
        return repository.expire_memories(connection, conversation_id, consistency.EXPIRED, to_utc_text(now))


def _proposal(item: ExtractedMemory, sources: Sequence[WindowMessage], language: str) -> Proposal:
    content = item.content.strip()
    key = consistency.subject_key(item.subject)
    by_user = any(source.author is Author.USER for source in sources)
    by_character = any(source.author is Author.CHARACTER for source in sources)
    provenance = consistency.checked_provenance(
        item.provenance, item.kind, key, by_user=by_user, by_character=by_character
    )
    cues = phrase_patterns(language, consistency.CHANGE_CUE_FILE)
    temporary = item.relation == "temporary" or item.durability == "temporary"
    return Proposal(
        relation=item.relation,
        kind=item.kind,
        content=content,
        subject_key=key,
        value_text=consistency.value_text(item.value),
        provenance=provenance,
        durability="temporary" if temporary else "lasting",
        importance=item.importance,
        confidence=stored_confidence(item.confidence, provenance),
        fingerprint=fingerprint(content, language),
        language=language,
        has_user_source=by_user,
        source_max_seq=max(source.seq for source in sources),
        change_cue=any(source.author is Author.USER and is_significant(source.content, cues) for source in sources),
    )


def _blocked(connection: Connection, scope: RunScope, proposal: Proposal, sources: Sequence[WindowMessage]) -> bool:
    """Guard 6: tells whether a tombstone of this conversation stops the candidate (consistency.is_blocked)."""
    hashed = consistency.value_hash(proposal.value_text, scope.language)
    tombstones = repository.matching_tombstones(
        connection, scope.character_id, scope.conversation_id, proposal.fingerprint, proposal.subject_key, hashed
    )
    return consistency.is_blocked(
        tombstones,
        fingerprint=proposal.fingerprint,
        subject_key=proposal.subject_key,
        value_hash=hashed,
        sources=[SourceRef(source.seq, source.content_hash) for source in sources],
    )


def _confirm(
    connection: Connection, memory: KnownMemory, importance: int, sources: Sequence[WindowMessage], now: str
) -> Applied:
    """A duplicate: the known memory gains the sources and the higher importance, with no new version. A locked memory
    gains the sources only, because automation never changes it."""
    added = repository.add_memory_sources(connection, memory.id, sources)
    if not memory.locked:
        repository.raise_memory_importance(connection, memory.id, importance, now)
    return Applied(memory_id=memory.id, added=False, sources_added=added)


def _insert(
    connection: Connection,
    scope: RunScope,
    item: ExtractedMemory,
    proposal: Proposal,
    decision: Decision,
    sources: Sequence[WindowMessage],
) -> Applied:
    """Writes a decision that inserts: first the changes to existing memories, each with its revision, then the new
    memory with its sources and index row, then the links of what it superseded. Superseding first frees the
    fingerprint of a replaced target, and linking last satisfies the foreign key.

    An `invalidated` memory of the conversation with the item's fingerprint is brought back instead of inserting
    another row: a revision, the decision's status, the new sources, and its stale sources dropped. A temporary item
    that a re-evaluation finds already past its expiry is stored `expired`."""
    now = to_utc_text(scope.now)
    valid_from = max(source.created_at for source in sources)
    status = decision.status
    expires_at = None
    if proposal.durability == "temporary":
        base = from_utc_text(valid_from) if scope.reevaluation else scope.now
        ends = consistency.expiry(item.valid_until, base)
        expires_at = to_utc_text(ends)
        if ends <= scope.now and status in _IN_USE:
            status = consistency.EXPIRED
    newer = decision.superseded_by
    valid_to = None if newer is None else (newer.valid_from or valid_from)
    group_id = (decision.conflict_group_id or new_id()) if decision.in_conflict else None
    for change in decision.supersede:
        repository.supersede_memory(
            connection, change.memory.id, valid_to=valid_from, reason=change.reason, run_id=scope.run_id, now=now
        )
    for changes, uncertain in ((decision.demote, True), (decision.group, False)):
        for change in changes:
            assert group_id is not None, "a decision with members is in conflict"
            repository.join_conflict(
                connection,
                change.memory.id,
                group_id,
                uncertain=uncertain,
                reason=change.reason,
                run_id=scope.run_id,
                now=now,
            )
    revived = repository.invalidated_by_fingerprint(
        connection, scope.character_id, scope.conversation_id, proposal.fingerprint
    )
    memory = repository.NewMemory(
        id=revived or new_id(),
        character_id=scope.character_id,
        conversation_id=scope.conversation_id,
        kind=proposal.kind,
        content=proposal.content,
        subject_key=proposal.subject_key,
        value_text=proposal.value_text,
        provenance=decision.provenance,
        durability=proposal.durability,
        importance=proposal.importance,
        confidence=decision.confidence,
        valid_from=valid_from,
        expires_at=expires_at,
        fingerprint=proposal.fingerprint,
        language=scope.language,
        status=status,
        needs_review=decision.needs_review,
        conflict_group_id=group_id,
        valid_to=valid_to,
    )
    if revived is None:
        repository.insert_memory(connection, memory, now)
        if newer is not None:
            repository.link_superseded(connection, [memory.id], newer.id)
    else:
        repository.reactivate_memory(
            connection,
            revived,
            memory,
            superseded_by_id=None if newer is None else newer.id,
            reason=consistency.REEVALUATION,
            run_id=scope.run_id,
            now=now,
        )
        repository.drop_stale_memory_sources(connection, revived)
    repository.link_superseded(connection, [change.memory.id for change in decision.supersede], memory.id)
    added = repository.add_memory_sources(connection, memory.id, sources)
    stored = repository.known_memory(connection, memory.id)
    assert stored is not None, "the memory was written above"
    index_memory(connection, memory.id, stored.content, stored.subject_key, scope.language)
    members = (*decision.demote, *decision.group)
    importance = max([proposal.importance, *(change.memory.importance for change in members)])
    return Applied(
        memory_id=memory.id,
        added=True,
        sources_added=added,
        superseded=len(decision.supersede),
        uncertain=len(decision.demote),
        conflict=status == "uncertain" and importance >= CONFLICT_EVENT_IMPORTANCE,
        reactivated=revived is not None,
    )
