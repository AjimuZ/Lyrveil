"""Pure rules of memory retrieval: the score of a candidate, the selection within a token budget, the prompt labels
and the past-intent cues (docs/memory-engine.md, Retrieval; ADR 0011). No I/O except the cached cue lists."""

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from lyrveil.memory.text import stems
from lyrveil.memory.window import is_significant, phrase_patterns
from lyrveil.ports.memory_retriever import ItemType, RetrievedMemory, ScoreParts

# The phrase list, per language under `prompts/<code>/`, of the cues that the user asks about the past.
PAST_CUE_FILE = "past_cues.txt"

EPISODE_KIND = "episode"
# The candidate routes of docs/memory-engine.md (Candidates).
MAX_FTS_CANDIDATES = 64
MAX_STANDING_CANDIDATES = 16
# Episodes come from their own index; three at most can be selected, so a few more candidates are enough to rank.
MAX_EPISODE_CANDIDATES = 16
MAX_PER_KIND = 6
MAX_EPISODES = 3
# The statuses a prompt may use; `superseded` only joins under past intent.
IN_USE_STATUSES = ("active", "uncertain")
PAST_STATUS = "superseded"

KIND_SCORES = {
    "boundary": 1.0,
    "promise": 0.9,
    "open_thread": 0.9,
    "user_preference": 0.8,
    "user_fact": 0.8,
    "decision": 0.7,
    "narrative_event": 0.6,
    "character_fact": 0.6,
    # Not in the scoring table: an episode is a narrative event, so it scores like one.
    EPISODE_KIND: 0.6,
}
STATUS_FACTORS = {"active": 1.0, "uncertain": 0.8, "superseded": 0.7}
LOCKED_BONUS = 0.10
PREVIOUS_BONUS = 0.05
# Locked memories and importance 5 never look older than this.
RECENCY_FLOOR = 0.5

LABEL_SURE = "[sicuro]"
LABEL_UNSURE = "[incerto]"
LABEL_STORY = "[storia]"
LABEL_FOR_NOW = "[per ora]"
LABEL_PAST = "[passato]"
LABEL_EPISODE = "[episodio]"


@dataclass(frozen=True)
class RetrievalWeights:
    """The weights of the score parts (settings `memory.retrieval.weight_*`) and the recency half-life in days.
    Weights are used normalized, so only their ratios matter; at least one must be above 0."""

    fts: float = 0.40
    importance: float = 0.20
    recency: float = 0.15
    topic: float = 0.10
    kind: float = 0.10
    confidence: float = 0.05
    recency_half_life_days: float = 30.0

    def normalized(self) -> tuple[float, float, float, float, float, float]:
        """Returns the six weights divided by their sum, in field order. Raises ValueError when a weight is negative,
        all are 0 or the half-life is not positive."""
        weights = (self.fts, self.importance, self.recency, self.topic, self.kind, self.confidence)
        total = sum(weights)
        if any(weight < 0 for weight in weights) or total <= 0 or self.recency_half_life_days <= 0:
            raise ValueError("retrieval weights must be non-negative with a positive sum and half-life")
        fts, importance, recency, topic, kind, confidence = (weight / total for weight in weights)
        return fts, importance, recency, topic, kind, confidence


@dataclass(frozen=True)
class Candidate:
    """A memory or episode that passed the SQL filters.

    `content` is the text a prompt shows (for an episode, its title and description). `kind` is the memory kind or
    `episode`. `bm25` is `-bm25()` of the item's index row for the query, higher is better, or None when the row does
    not match. `confidence` is 1.0 for an episode, which stores none.
    """

    id: str
    item_type: ItemType
    kind: str
    content: str
    subject_key: str | None
    provenance: str
    durability: str
    status: str
    importance: int
    confidence: float
    locked: bool
    conflict_group_id: str | None
    language: str
    updated_at: datetime
    bm25: float | None


@dataclass(frozen=True)
class Scored:
    """A candidate with its final score, in 0 to 1, and the parts of that score."""

    candidate: Candidate
    score: float
    parts: ScoreParts


def is_past_intent(text: str, language: str | None) -> bool:
    """Tells whether the user's message asks about the past: it holds a cue of `prompts/<code>/past_cues.txt`.
    A language without a list never has past intent."""
    return is_significant(text, phrase_patterns(language, PAST_CUE_FILE))


def fts_parts(candidates: Iterable[Candidate]) -> dict[str, float]:
    """Returns the `fts` part of each candidate: 0 without a match, else `0.5 + 0.5 * (s - s_min) / (s_max - s_min)`
    over the matched candidates of the same item type, and 1.0 when they all have the same `s`.

    BM25 is normalized within the candidate set because its absolute values are near zero on small tables (ADR 0011).
    Memories and episodes come from two indexes, so each type is normalized on its own.
    """
    parts: dict[str, float] = {}
    by_type: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        if candidate.bm25 is None:
            parts[candidate.id] = 0.0
        else:
            by_type.setdefault(candidate.item_type, []).append(candidate)
    for matched in by_type.values():
        low = min(candidate.bm25 or 0.0 for candidate in matched)
        high = max(candidate.bm25 or 0.0 for candidate in matched)
        for candidate in matched:
            value = candidate.bm25 or 0.0
            parts[candidate.id] = 1.0 if high == low else 0.5 + 0.5 * (value - low) / (high - low)
    return parts


def topic_part(topic_stems: frozenset[str], candidate: Candidate) -> float:
    """Returns the share of the topic's distinct stems found in the candidate's text or subject key; 0 without a
    topic."""
    if not topic_stems:
        return 0.0
    own = set(stems(candidate.content, candidate.language))
    own.update(stems(candidate.subject_key or "", candidate.language))
    return len(topic_stems & own) / len(topic_stems)


def recency_part(candidate: Candidate, now: datetime, half_life_days: float) -> float:
    """Returns `exp(-ln2 * age_days / half_life_days)` of the candidate's `updated_at`, 1.0 for a future time, and at
    least RECENCY_FLOOR for a locked candidate or one of importance 5."""
    age_days = max(0.0, (now - candidate.updated_at).total_seconds() / 86400)
    value = math.exp(-math.log(2) * age_days / half_life_days)
    if candidate.locked or candidate.importance >= 5:
        value = max(value, RECENCY_FLOOR)
    return value


def score_all(
    candidates: Sequence[Candidate],
    *,
    topic_stems: frozenset[str],
    now: datetime,
    previous_ids: frozenset[str],
    weights: RetrievalWeights,
) -> list[Scored]:
    """Scores every candidate (docs/memory-engine.md, Scoring) and returns them in input order.

    `score = (w_fts fts + w_imp importance + w_rec recency + w_top topic + w_kind kind + w_conf confidence)
    * status_factor`, then `min(1, score + 0.10 if locked + 0.05 if in previous_ids)`. The weights are normalized.
    """
    w_fts, w_importance, w_recency, w_topic, w_kind, w_confidence = weights.normalized()
    fts = fts_parts(candidates)
    scored: list[Scored] = []
    for candidate in candidates:
        parts = ScoreParts(
            fts=fts[candidate.id],
            importance=(min(5, max(1, candidate.importance)) - 1) / 4,
            recency=recency_part(candidate, now, weights.recency_half_life_days),
            topic=topic_part(topic_stems, candidate),
            kind=KIND_SCORES.get(candidate.kind, 0.0),
            confidence=min(1.0, max(0.0, candidate.confidence)),
            status_factor=STATUS_FACTORS.get(candidate.status, 0.0),
            locked_bonus=LOCKED_BONUS if candidate.locked else 0.0,
            previous_bonus=PREVIOUS_BONUS if candidate.id in previous_ids else 0.0,
        )
        base = (
            w_fts * parts.fts
            + w_importance * parts.importance
            + w_recency * parts.recency
            + w_topic * parts.topic
            + w_kind * parts.kind
            + w_confidence * parts.confidence
        )
        score = min(1.0, base * parts.status_factor + parts.locked_bonus + parts.previous_bonus)
        scored.append(Scored(candidate=candidate, score=score, parts=parts))
    return scored


def label(candidate: Candidate) -> str:
    """Returns the prompt label of docs/memory-engine.md (Prompt rendering).

    The first rule that applies wins: an episode; a superseded memory (only selected under past intent); an uncertain
    one, so both sides of a conflict read `[incerto]`; a fictional one; a temporary one; an inference; otherwise
    `[sicuro]` (stated or confirmed, active).
    """
    if candidate.item_type == "episode":
        return LABEL_EPISODE
    if candidate.status == PAST_STATUS:
        return LABEL_PAST
    if candidate.status == "uncertain":
        return LABEL_UNSURE
    if candidate.provenance == "fictional":
        return LABEL_STORY
    if candidate.durability == "temporary":
        return LABEL_FOR_NOW
    if candidate.provenance == "inferred":
        return LABEL_UNSURE
    return LABEL_SURE


def render_line(candidate: Candidate) -> str:
    """Returns the candidate's line of the "Ricordi pertinenti" block: `- <label> <content>`."""
    return f"- {label(candidate)} {candidate.content}"


def estimate_tokens(text: str) -> int:
    """Returns a deliberately high estimate of the tokens of `text`: one per three characters, rounded up, plus one
    for the line break. It only packs the memory block; the final prompt is always counted by the runtime
    (docs/memory-engine.md, Token counting), which cuts the lowest-scoring memories when over budget."""
    return -(-len(text) // 3) + 1


def _subject(candidate: Candidate) -> tuple[str, bool] | None:
    # A superseded memory under past intent does not compete with the memory in use of its subject, so the prompt
    # can show "[passato]" next to the current fact.
    if candidate.subject_key is None:
        return None
    return candidate.subject_key, candidate.status == PAST_STATUS


def _ordered(scored: Iterable[Scored]) -> list[Scored]:
    # Ties go to memories before episodes, then to the identifier, so the selection is deterministic.
    return sorted(scored, key=lambda item: (-item.score, item.candidate.item_type != "memory", item.candidate.id))


def _units(ordered: Sequence[Scored]) -> list[list[Scored]]:
    """Steps 1 and 2 of Selection: groups the members of one conflict group (uncertain memories) into one unit placed
    where its best member is, and keeps only the best unit of each subject."""
    groups: dict[str, list[Scored]] = {}
    for item in ordered:
        group = item.candidate.conflict_group_id
        if group is not None and item.candidate.status == "uncertain":
            groups.setdefault(group, []).append(item)
    units: list[list[Scored]] = []
    seen: set[str] = set()
    subjects: set[tuple[str, bool]] = set()
    for item in ordered:
        if item.candidate.id in seen:
            continue
        group = item.candidate.conflict_group_id
        unit = groups[group] if group is not None and item.candidate.status == "uncertain" else [item]
        seen.update(member.candidate.id for member in unit)
        keys = {key for member in unit if (key := _subject(member.candidate)) is not None}
        if keys & subjects:
            continue
        subjects |= keys
        units.append(unit)
    return units


def select(
    scored: Iterable[Scored], *, token_budget: int, max_items: int, token_cost: Callable[[str], int]
) -> list[RetrievedMemory]:
    """Returns the selection of docs/memory-engine.md (Selection), best first.

    Sorts by score; keeps the best memory of each subject (a superseded one under past intent is kept beside the one in
    use); keeps the members of a conflict group together, all or none; allows at most MAX_PER_KIND memories per kind
    and MAX_EPISODES episodes; then adds each unit whose lines fit what is left of `token_budget` and of `max_items`.
    A unit that does not fit is skipped and smaller ones after it may still enter. `token_cost` prices one rendered
    line. A budget or maximum of 0 or less gives an empty list.
    """
    selected: list[RetrievedMemory] = []
    if token_budget <= 0 or max_items <= 0:
        return selected
    spent = 0
    per_kind: dict[str, int] = {}
    for unit in _units(_ordered(scored)):
        lines = [(member, render_line(member.candidate)) for member in unit]
        costs = [token_cost(line) for _, line in lines]
        wanted: dict[str, int] = {}
        for member in unit:
            wanted[member.candidate.kind] = wanted.get(member.candidate.kind, 0) + 1
        over_kind = any(
            per_kind.get(kind, 0) + count > (MAX_EPISODES if kind == EPISODE_KIND else MAX_PER_KIND)
            for kind, count in wanted.items()
        )
        if over_kind or len(selected) + len(unit) > max_items or spent + sum(costs) > token_budget:
            continue
        for (member, line), cost in zip(lines, costs, strict=True):
            candidate = member.candidate
            selected.append(
                RetrievedMemory(
                    id=candidate.id,
                    item_type=candidate.item_type,
                    kind=candidate.kind,
                    content=candidate.content,
                    status=candidate.status,
                    label=label(candidate),
                    line=line,
                    score=member.score,
                    parts=member.parts,
                    token_cost=cost,
                    subject_key=candidate.subject_key,
                    conflict_group_id=candidate.conflict_group_id,
                )
            )
        spent += sum(costs)
        for kind, count in wanted.items():
            per_kind[kind] = per_kind.get(kind, 0) + count
    return selected
