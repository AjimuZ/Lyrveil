"""The consistency rules without a database (lyrveil.memory.consistency): deduplication by fingerprint and by
similarity (T-MEM-04), the inference limit and the provenance check (T-MEM-14), and the other rules `decide` applies
before MemoryConsistencyService writes anything."""

from dataclasses import replace
from typing import Any

from lyrveil.domain.memory_extraction import stored_confidence
from lyrveil.memory.consistency import (
    INFERENCE_CONFLICT,
    SIMILARITY_THRESHOLD,
    SUBJECT_CHANGE,
    SUBJECT_CONFLICT,
    KnownMemory,
    Proposal,
    SourceChange,
    SourceRef,
    Tombstone,
    after_source_change,
    checked_provenance,
    decide,
    is_blocked,
    jaccard,
    stem_set,
    value_hash,
)
from lyrveil.memory.text import fingerprint

LANGUAGE = "it"
ROSSI = "Lavora allo Studio Rossi."
VERDI = "Lavora da Verdi & Co."
_KNOWN = KnownMemory(
    id="e1",
    kind="user_fact",
    content=ROSSI,
    subject_key="user.employer",
    value_text="Studio Rossi",
    provenance="stated",
    durability="lasting",
    status="active",
    importance=3,
    locked=False,
    conflict_group_id=None,
    fingerprint=fingerprint(ROSSI, LANGUAGE),
    source_max_seq=5,
)
_PROPOSAL = Proposal(
    relation="new",
    kind="user_fact",
    content=VERDI,
    subject_key="user.employer",
    value_text="Verdi & Co",
    provenance="stated",
    durability="lasting",
    importance=4,
    confidence=0.95,
    fingerprint=fingerprint(VERDI, LANGUAGE),
    language=LANGUAGE,
    has_user_source=True,
    source_max_seq=20,
    change_cue=False,
)


def known(content: str = ROSSI, **changes: Any) -> KnownMemory:
    return replace(_KNOWN, content=content, fingerprint=fingerprint(content, LANGUAGE), **changes)


def proposal(content: str = VERDI, **changes: Any) -> Proposal:
    return replace(_PROPOSAL, content=content, fingerprint=fingerprint(content, LANGUAGE), **changes)


def test_the_same_fingerprint_is_a_duplicate() -> None:
    """T-MEM-04, fingerprint: other words for the same stems confirm the memory in use instead of adding one."""
    same = known("Lavora allo Studio Rossi")
    decision = decide(proposal("lavora, allo studio ROSSI!", value_text="Studio Rossi"), None, same, [], [])
    assert decision.confirm == same
    assert (decision.supersede, decision.demote, decision.group) == ((), (), ())


def test_a_near_duplicate_of_the_same_kind_and_subject_is_a_duplicate() -> None:
    """T-MEM-04, similarity: a stem-set Jaccard of at least 0.8 with the same kind and subject is a duplicate; another
    kind, another subject or another value is not."""
    stored = known("Corre nel parco ogni mattina con il suo cane Fido.", subject_key=None, value_text=None)
    text = "Corre nel parco ogni mattina con il cane Fido, anche d'inverno."
    score = jaccard(stem_set(text, LANGUAGE), stem_set(stored.content, LANGUAGE))
    assert SIMILARITY_THRESHOLD <= score < 1.0, score
    item = proposal(text, subject_key=None, value_text=None)

    assert decide(item, None, None, [stored], []).confirm == stored
    assert decide(replace(item, kind="user_preference"), None, None, [stored], []).confirm is None
    assert decide(replace(item, subject_key="user.habit"), None, None, [stored], []).confirm is None
    far = proposal("Corre la sera.", subject_key=None, value_text=None)
    assert decide(far, None, None, [stored], []).confirm is None

    with_value = known(stored.content, subject_key="user.dog", value_text="Fido")
    other_dog = replace(item, subject_key="user.dog", value_text="Rex")
    assert decide(other_dog, None, None, [with_value], []).confirm is None, "another value is another fact"


def test_jaccard_of_stem_sets() -> None:
    assert jaccard(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0
    assert jaccard(frozenset({"a", "b", "c", "d"}), frozenset({"a", "b", "c", "d", "e"})) == 0.8
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_an_inference_never_supersedes_a_stated_fact() -> None:
    """T-MEM-14: an inferred update of a stated memory is stored uncertain in a conflict group with it, and the
    stated memory keeps its status; an inferred memory may still replace another inference."""
    stated = known()
    guessed = proposal(relation="update", provenance="inferred", confidence=0.6)

    decision = decide(guessed, stated, None, [], [stated])
    assert decision.confirm is None
    assert (decision.status, decision.provenance, decision.confidence) == ("uncertain", "inferred", 0.6)
    assert decision.supersede == ()
    assert decision.demote == ()
    assert [(change.memory.id, change.reason) for change in decision.group] == [("e1", INFERENCE_CONFLICT)]
    assert decision.in_conflict

    assert decide(guessed, replace(stated, provenance="user_confirmed"), None, [], []).supersede == ()
    replaced = decide(guessed, replace(stated, provenance="inferred"), None, [], [])
    assert [change.memory.id for change in replaced.supersede] == ["e1"]
    assert replaced.status == "active"


def test_unsourced_statements_are_downgraded_and_inferences_capped() -> None:
    """T-MEM-14: a stated fact about the user needs a message by the user, otherwise it is an inference whose
    confidence is capped at 0.6; what the character says about itself is fiction."""
    downgraded = checked_provenance("stated", "user_fact", "user.city", by_user=False, by_character=True)
    assert downgraded == "inferred"
    assert stored_confidence("high", downgraded) == 0.6
    assert checked_provenance("stated", "boundary", "user.topics", by_user=False, by_character=True) == "inferred"
    assert checked_provenance("stated", "user_fact", "user.city", by_user=True, by_character=True) == "stated"
    assert checked_provenance("stated", "character_fact", None, by_user=False, by_character=True) == "fictional"
    assert checked_provenance("stated", "character_fact", None, by_user=True, by_character=False) == "stated"
    assert checked_provenance("inferred", "user_fact", None, by_user=True, by_character=False) == "inferred"


def test_a_single_valued_subject_never_keeps_two_active_values() -> None:
    """Guard 3: a `new` value of `user.employer` contradicts the active one, unless a change cue makes it an update."""
    rival = known()
    unclear = decide(proposal(), None, None, [], [rival])
    assert unclear.status == "uncertain"
    assert [(change.memory.id, change.reason) for change in unclear.demote] == [("e1", SUBJECT_CONFLICT)]

    changed = decide(proposal(change_cue=True), None, None, [], [rival])
    assert changed.status == "active"
    assert [(change.memory.id, change.reason) for change in changed.supersede] == [("e1", SUBJECT_CHANGE)]

    same_value = decide(proposal("È impiegata allo Studio Rossi.", value_text="studio rossi"), None, None, [], [rival])
    assert (same_value.status, same_value.demote, same_value.supersede) == ("active", (), ())
    temporary = decide(proposal(durability="temporary"), None, None, [], [rival])
    assert (temporary.status, temporary.demote, temporary.supersede) == ("active", (), ())


def test_updates_need_the_same_subject_and_a_newer_source() -> None:
    target = known()
    update = proposal(relation="update")
    assert [change.memory.id for change in decide(update, target, None, [], []).supersede] == ["e1"]
    older = decide(replace(update, source_max_seq=5), target, None, [], [])
    assert (older.status, [change.memory.id for change in older.demote]) == ("uncertain", ["e1"])
    elsewhere = decide(replace(update, subject_key="user.city", value_text="Torino"), target, None, [], [])
    assert (elsewhere.status, elsewhere.supersede, elsewhere.demote) == ("active", (), ())


def test_a_user_correction_wins_and_is_confirmed() -> None:
    target = known()
    correction = proposal(relation="correction", confidence=0.7)
    decision = decide(correction, target, None, [], [])
    assert (decision.status, decision.provenance, decision.confidence) == ("active", "user_confirmed", 1.0)
    assert [change.memory.id for change in decision.supersede] == ["e1"]

    by_character = decide(replace(correction, has_user_source=False), target, None, [], [])
    assert (by_character.status, by_character.provenance) == ("uncertain", "stated")
    assert [change.memory.id for change in by_character.demote] == ["e1"], "unclear without the user's word"


def test_locked_memories_temporary_items_and_identity_candidates_change_nothing() -> None:
    target = known()
    locked = decide(proposal(relation="update"), replace(target, locked=True), None, [], [])
    assert (locked.status, locked.needs_review, locked.supersede, locked.group) == ("uncertain", True, (), ())

    temporary = decide(proposal(relation="update", durability="temporary"), target, None, [], [])
    assert (temporary.status, temporary.supersede, temporary.demote) == ("active", (), ())

    candidate = proposal("Dario ha 40 anni.", relation="update", kind="identity_candidate", subject_key="character.age")
    decision = decide(candidate, target, None, [], [target])
    assert (decision.status, decision.supersede, decision.demote, decision.group) == ("active", (), (), ())


def test_a_target_with_the_same_words_that_stays_in_use_is_confirmed() -> None:
    """The fingerprint index allows one memory in use per text: a contradiction worded like its target confirms it."""
    target = known()
    decision = decide(proposal(ROSSI, relation="contradiction", value_text="Studio Rossi"), target, target, [], [])
    assert decision.confirm == target


def test_a_tombstone_blocks_a_candidate_whose_every_source_is_old_or_known() -> None:
    """Guard 6: same text, or same subject and value, with no source newer than the deleted memory's (T-MEM-09)."""
    tombstone = Tombstone(
        fingerprint=fingerprint(ROSSI, LANGUAGE),
        subject_key="user.employer",
        value_hash=value_hash("Studio Rossi", LANGUAGE),
        source_hashes=frozenset({"h1"}),
        source_max_seq=5,
    )

    def blocked(text: str, value: str | None, *sources: SourceRef) -> bool:
        return is_blocked(
            [tombstone],
            fingerprint=fingerprint(text, LANGUAGE),
            subject_key="user.employer",
            value_hash=value_hash(value, LANGUAGE),
            sources=sources,
        )

    assert blocked(ROSSI, "Studio Rossi", SourceRef(5, "h9"))
    assert blocked("Adesso lavora allo Studio Rossi.", "studio rossi.", SourceRef(2, None)), "same subject and value"
    assert blocked(ROSSI, None, SourceRef(9, "h1")), "a newer message with a listed content hash"
    assert not blocked(ROSSI, "Studio Rossi", SourceRef(6, "h9")), "a newer, new source is fresh information"
    assert not blocked(ROSSI, "Studio Rossi", SourceRef(2, "h9"), SourceRef(6, "h9")), "one new source is enough"
    assert not blocked(VERDI, "Verdi & Co", SourceRef(2, "h1")), "another fact is not blocked"
    assert not is_blocked([], fingerprint="x", subject_key=None, value_hash=None, sources=[SourceRef(1, None)]), (
        "nothing deleted, nothing blocked"
    )


def test_an_item_older_than_the_memory_it_would_change_is_stored_as_history() -> None:
    """A re-evaluation reads old messages: an item whose sources all come before a memory's newest source never
    supersedes or demotes that memory, whether it targets it or meets it through guard 3. It is stored superseded by
    the newest such memory, and a target as old as the item still makes a contradiction."""
    newer = known(VERDI, value_text="Verdi & Co", source_max_seq=30, valid_from="2026-06-01T00:00:00.000Z")
    old = proposal(ROSSI, value_text="Studio Rossi", source_max_seq=10)

    targeted = decide(replace(old, relation="update"), newer, None, [], [])
    met = decide(old, None, None, [], [newer])
    for decision in (targeted, met):
        assert (decision.status, decision.superseded_by) == ("superseded", newer)
        assert (decision.supersede, decision.demote, decision.group, decision.in_conflict) == ((), (), (), False)

    same_age = decide(replace(old, relation="update", source_max_seq=30), newer, None, [], [])
    assert (same_age.status, same_age.superseded_by) == ("uncertain", None)
    assert [change.memory.id for change in same_age.demote] == ["e1"]


def test_a_changed_source_flags_weakens_or_invalidates_a_memory() -> None:
    """docs/memory-engine.md (Invalidation after message changes), step 2."""

    def change(
        locked: bool = False, left: int = 1, provenance: str = "stated", confidence: float = 0.95
    ) -> SourceChange:
        return after_source_change(locked=locked, valid_sources_left=left, provenance=provenance, confidence=confidence)

    assert change(locked=True, left=0) == SourceChange("review", 0.95), "a locked memory is only flagged"
    assert change() == SourceChange("weaken", 0.85)
    assert change(left=2, provenance="inferred", confidence=0.05) == SourceChange("weaken", 0.0), "never below 0"
    assert change(provenance="user_confirmed", confidence=1.0) == SourceChange("weaken", 1.0), "tied to 1.0"
    assert change(left=0).action == "invalidate"
