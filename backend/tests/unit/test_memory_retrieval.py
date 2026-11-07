"""The pure retrieval rules of lyrveil.memory.retrieval: BM25 normalization, recency, labels, weights, past intent."""

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from lyrveil.memory.retrieval import (
    Candidate,
    RetrievalWeights,
    fts_parts,
    is_past_intent,
    label,
    recency_part,
    score_all,
)

NOW = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
BASE = Candidate(
    id="m",
    item_type="memory",
    kind="user_fact",
    content="Lavora allo Studio Rossi.",
    subject_key="user.employer",
    provenance="stated",
    durability="lasting",
    status="active",
    importance=3,
    confidence=0.95,
    locked=False,
    conflict_group_id=None,
    language="it",
    updated_at=NOW,
    bm25=None,
)


def test_bm25_is_normalized_within_each_item_type() -> None:
    candidates = [
        replace(BASE, id="a", bm25=1.0),
        replace(BASE, id="b", bm25=2.0),
        replace(BASE, id="c", bm25=3.0),
        replace(BASE, id="none"),
        replace(BASE, id="episode", item_type="episode", kind="episode", bm25=0.001),
    ]
    assert fts_parts(candidates) == {"a": 0.5, "b": 0.75, "c": 1.0, "none": 0.0, "episode": 1.0}


def test_recency_halves_every_half_life_with_a_floor_for_locked_and_important_memories() -> None:
    old = replace(BASE, updated_at=NOW - timedelta(days=60))
    assert recency_part(old, NOW, 30) == pytest.approx(0.25)
    assert recency_part(replace(old, locked=True), NOW, 30) == 0.5
    assert recency_part(replace(old, importance=5), NOW, 30) == 0.5
    assert recency_part(replace(BASE, updated_at=NOW + timedelta(days=1)), NOW, 30) == 1.0


def test_the_score_is_capped_at_one_and_weights_are_normalized() -> None:
    best = replace(BASE, importance=5, confidence=1.0, kind="boundary", bm25=1.0, locked=True)
    (scored,) = score_all(
        [best], topic_stems=frozenset({"rossi"}), now=NOW, previous_ids=frozenset({"m"}), weights=RetrievalWeights()
    )
    assert scored.score == 1.0
    doubled = RetrievalWeights(fts=0.8, importance=0.4, recency=0.3, topic=0.2, kind=0.2, confidence=0.1)
    plain = replace(BASE, bm25=1.0)
    one = score_all([plain], topic_stems=frozenset(), now=NOW, previous_ids=frozenset(), weights=RetrievalWeights())
    two = score_all([plain], topic_stems=frozenset(), now=NOW, previous_ids=frozenset(), weights=doubled)
    assert one[0].score == pytest.approx(two[0].score)
    assert one[0].score == pytest.approx(0.40 + 0.20 * 0.5 + 0.15 + 0.10 * 0.8 + 0.05 * 0.95)
    with pytest.raises(ValueError, match="retrieval weights"):
        RetrievalWeights(fts=0, importance=0, recency=0, topic=0, kind=0, confidence=0).normalized()
    with pytest.raises(ValueError, match="retrieval weights"):
        RetrievalWeights(fts=-1).normalized()
    assert math.isclose(sum(RetrievalWeights().normalized()), 1.0)


def test_labels_follow_the_prompt_rendering_table() -> None:
    assert label(BASE) == "[sicuro]"
    assert label(replace(BASE, provenance="user_confirmed")) == "[sicuro]"
    assert label(replace(BASE, provenance="inferred")) == "[incerto]"
    assert label(replace(BASE, status="uncertain", provenance="fictional")) == "[incerto]"
    assert label(replace(BASE, provenance="fictional")) == "[storia]"
    assert label(replace(BASE, durability="temporary")) == "[per ora]"
    assert label(replace(BASE, status="superseded")) == "[passato]"
    assert label(replace(BASE, item_type="episode", kind="episode")) == "[episodio]"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Ti ricordi quando siamo andati al mare?", True),
        ("Prima lavoravo a Genova.", True),
        ("L'anno scorso abitavo a Roma.", True),
        ("UNA VOLTA ho visto un delfino.", True),
        ("Che bella giornata oggi.", False),
    ],
)
def test_past_intent_reads_the_italian_cues(message: str, expected: bool) -> None:
    assert is_past_intent(message, "it-IT") is expected


def test_a_language_without_cues_never_has_past_intent() -> None:
    assert not is_past_intent("Do you remember when we met?", "en")
    assert not is_past_intent("Ti ricordi quando?", None)
