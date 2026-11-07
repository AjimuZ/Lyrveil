"""The Character State Manager's rules (docs/consistency-engine.md, Character State Manager): the bounded deltas of
relationship events, the changes one extraction run asks for, and the conversation state as a pure fold over its
applied history rows. No database and no clock: CharacterStateManager reads the rows and writes the result.

The fold is the only way a state is computed. A run folds the applied rows with its new ones, and a replay after an
invalidation folds the rows that remain, so the stored state is always the fold of the conversation's applied rows: an
invalidated row leaves no trace, not even in the limits the other rows met, and two chats never affect each other
because each fold reads the rows of one conversation.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, tzinfo
from types import MappingProxyType

from lyrveil.domain.identifiers import from_utc_text
from lyrveil.domain.memory_extraction import RelationshipEvent, StateUpdate
from lyrveil.memory.consistency import SIMILARITY_THRESHOLD, jaccard, stem_set
from lyrveil.memory.text import fingerprint

DIMENSIONS = ("familiarity", "trust", "confidence", "tension")

# `character_state_history.event_kind` and `status` (docs/data-model.md).
RELATIONSHIP_CHANGE = "relationship_change"
EMOTION_CHANGE = "emotion_change"
THREAD_OPENED = "thread_opened"
THREAD_CLOSED = "thread_closed"
GOAL_CHANGE = "goal_change"
NARRATIVE_CHANGE = "narrative_change"
USER_CORRECTION = "user_correction"
APPLIED = "applied"
INVALIDATED = "invalidated"
# The kinds an extraction run writes. `user_correction` belongs to the user's corrections, which are not delivered.
RUN_KINDS = frozenset(
    {RELATIONSHIP_CHANGE, EMOTION_CHANGE, THREAD_OPENED, THREAD_CLOSED, GOAL_CHANGE, NARRATIVE_CHANGE}
)

# docs/consistency-engine.md (Character State Manager): the values a conversation starts from.
INITIAL_VALUES: Mapping[str, int] = MappingProxyType({"familiarity": 10, "trust": 20, "confidence": 10, "tension": 0})
# Update rules: the size of each magnitude, and the importance of an episode of the same run that a `large` one needs.
MAGNITUDE_DELTAS: Mapping[str, int] = MappingProxyType({"small": 2, "medium": 4, "large": 8})
LARGE_MIN_EPISODE_IMPORTANCE = 4
# The net change one dimension may take in one run and in one local calendar day, and its range.
RUN_LIMIT = 8
DAY_LIMIT = 15
MIN_VALUE = 0
MAX_VALUE = 100
# From numbers to text: the first value of each of the four bands.
BAND_STARTS = (0, 20, 40, 70)
# Not in the documents: the most items each list keeps (the oldest goes first) and the most one run adds to each, so
# block 4 and the row stay bounded whatever the model writes.
MAX_OPEN_THREADS = 6
MAX_GOALS = 5
MAX_NARRATIVE_CHANGES = 10
MAX_ITEMS_PER_RUN = 4
# A proposed text is kept on one line and only up to this length; a longer one is dropped rather than cut.
MAX_TEXT_CHARS = 200
# The episodes `relationship_states.recent_event_ids_json` lists, newest first.
RECENT_EVENTS = 5

# The reason of each text event. The StateUpdate of a run gives none, and the column needs one; the Memory Studio shows
# it in the change log.
UPDATE_REASONS: Mapping[str, str] = MappingProxyType(
    {
        EMOTION_CHANGE: "Lo stato d'animo che emerge dai messaggi.",
        THREAD_OPENED: "Un discorso rimasto in sospeso nei messaggi.",
        THREAD_CLOSED: "Un discorso in sospeso si è chiuso nei messaggi.",
        GOAL_CHANGE: "Un obiettivo emerso nei messaggi.",
        NARRATIVE_CHANGE: "Un cambiamento nella vostra storia.",
    }
)


@dataclass(frozen=True)
class StateEvent:
    """One applied row of `character_state_history`, as the fold reads it.

    `delta` is the requested change of a `relationship_change`: the signed size of its magnitude, never the applied
    one, which the fold computes again. `value_after` is the text a text event sets or adds, and `value_before` the open
    thread a `thread_closed` removes. `run_id` groups the per-run limit, and `created_at` (UTC text) gives the local day
    of the per-day limit.
    """

    id: str
    event_kind: str
    created_at: str
    run_id: str | None = None
    dimension: str | None = None
    delta: int | None = None
    value_before: str | None = None
    value_after: str | None = None


@dataclass(frozen=True)
class RelationshipState:
    """The state of one conversation: `relationship_states` without `recent_event_ids_json` and `updated_at`.

    The four values are internal: they never reach a prompt or the chat as numbers. Lists are oldest first.
    `last_history_id` is the newest row the fold read, None for the starting state.
    """

    familiarity: int = INITIAL_VALUES["familiarity"]
    trust: int = INITIAL_VALUES["trust"]
    confidence: int = INITIAL_VALUES["confidence"]
    tension: int = INITIAL_VALUES["tension"]
    narrative: str = ""
    expectations: str = ""
    boundaries: str = ""
    emotional_state: str | None = None
    current_topic: str | None = None
    open_threads: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()
    narrative_changes: tuple[str, ...] = ()
    last_history_id: str | None = None

    def value(self, dimension: str) -> int:
        """Returns the value of one of DIMENSIONS."""
        value: int = getattr(self, dimension)
        return value


@dataclass(frozen=True)
class Change:
    """What the fold made of one `relationship_change`: the value before and after it. `after - before` is the applied
    delta, which the limits may make smaller than the requested one, down to 0."""

    before: int
    after: int


@dataclass(frozen=True)
class Replay:
    """The state a fold reached, and the Change of each `relationship_change` it read, by row id."""

    state: RelationshipState
    changes: Mapping[str, Change]


@dataclass(frozen=True)
class Proposal:
    """One history row a run asks for, before it has an id and the fold gives it its values.

    Always holds (docs/consistency-engine.md, Update rules): `reason` is not blank and `sources` names at least one
    message; `event_kind` is one of RUN_KINDS; a `relationship_change` has a dimension and a `delta` of the size of one
    magnitude, and a text event has neither. Raises ValueError otherwise, so no row without a reason or sources can be
    asked for.
    """

    event_kind: str
    reason: str
    sources: tuple[str, ...]
    dimension: str | None = None
    delta: int | None = None
    value_before: str | None = None
    value_after: str | None = None

    def __post_init__(self) -> None:
        if self.event_kind not in RUN_KINDS:
            raise ValueError(f"a run cannot write a {self.event_kind} row")
        if not self.reason.strip():
            raise ValueError("a state change needs a reason")
        if not self.sources or not all(self.sources):
            raise ValueError("a state change needs its source messages")
        if self.event_kind == RELATIONSHIP_CHANGE:
            if self.dimension not in INITIAL_VALUES or self.delta is None:
                raise ValueError("a relationship change needs a dimension and a delta")
            if abs(self.delta) not in MAGNITUDE_DELTAS.values():
                raise ValueError("a relationship change moves by the size of one magnitude")
        elif self.dimension is not None or self.delta is not None:
            raise ValueError("only a relationship change moves a dimension")


@dataclass(frozen=True)
class Planned:
    """The text events of one run's StateUpdate, with the items it dropped and those it could not apply yet."""

    proposals: tuple[Proposal, ...]
    dropped: int
    deferred: int


def local_day(created_at: str, zone: tzinfo) -> date:
    """Returns the calendar day in `zone` of a stored UTC timestamp."""
    return from_utc_text(created_at).astimezone(zone).date()


def bounded(requested: int, value: int, run_total: int, day_total: int) -> int:
    """Returns the part of `requested` that the limits allow, with its sign, or 0.

    `run_total` and `day_total` are the net changes the dimension already took in this run and on this local day, and
    `value` is its current value. The result keeps both net changes within ±RUN_LIMIT and ±DAY_LIMIT and the value
    within MIN_VALUE to MAX_VALUE, and is never larger than `requested`.
    """
    if requested >= 0:
        return max(0, min(requested, RUN_LIMIT - run_total, DAY_LIMIT - day_total, MAX_VALUE - value))
    return min(0, max(requested, -RUN_LIMIT - run_total, -DAY_LIMIT - day_total, MIN_VALUE - value))


def _append(items: list[str], text: str | None, limit: int) -> None:
    # A text already listed is not repeated, and the oldest items go once the list is over its limit.
    if text and text not in items:
        items.append(text)
        del items[: max(0, len(items) - limit)]


def replay(events: Iterable[StateEvent], zone: tzinfo) -> Replay:
    """Folds `events`, the applied rows of one conversation in the order they were written, over the starting state.

    A `relationship_change` moves its dimension by what `bounded` allows of its requested `delta`, given the net change
    of that dimension so far in its run (a row without a run counts as a run of its own) and on its local day in
    `zone`. `emotion_change` sets the emotional state; `thread_opened`, `goal_change` and `narrative_change` add their
    text to their list; `thread_closed` removes its open thread, and does nothing when that thread is not open.
    `user_correction` rows are not written yet and change nothing. The same rows in the same order and zone always give
    the same Replay, and every value stays within MIN_VALUE to MAX_VALUE.
    """
    values = dict(INITIAL_VALUES)
    run_totals: dict[tuple[str, str], int] = {}
    day_totals: dict[tuple[date, str], int] = {}
    changes: dict[str, Change] = {}
    emotional_state: str | None = None
    threads: list[str] = []
    goals: list[str] = []
    narrative_changes: list[str] = []
    last_id: str | None = None
    for event in events:
        last_id = event.id
        kind = event.event_kind
        if kind == RELATIONSHIP_CHANGE and event.dimension in values and event.delta is not None:
            dimension = event.dimension
            run_key = (event.run_id or event.id, dimension)
            day_key = (local_day(event.created_at, zone), dimension)
            before = values[dimension]
            applied = bounded(event.delta, before, run_totals.get(run_key, 0), day_totals.get(day_key, 0))
            values[dimension] = before + applied
            run_totals[run_key] = run_totals.get(run_key, 0) + applied
            day_totals[day_key] = day_totals.get(day_key, 0) + applied
            changes[event.id] = Change(before, before + applied)
        elif kind == EMOTION_CHANGE:
            emotional_state = event.value_after or None
        elif kind == THREAD_OPENED:
            _append(threads, event.value_after, MAX_OPEN_THREADS)
        elif kind == THREAD_CLOSED and event.value_before in threads:
            threads.remove(event.value_before)
        elif kind == GOAL_CHANGE:
            _append(goals, event.value_after, MAX_GOALS)
        elif kind == NARRATIVE_CHANGE:
            _append(narrative_changes, event.value_after, MAX_NARRATIVE_CHANGES)
    state = RelationshipState(
        familiarity=values["familiarity"],
        trust=values["trust"],
        confidence=values["confidence"],
        tension=values["tension"],
        emotional_state=emotional_state,
        open_threads=tuple(threads),
        goals=tuple(goals),
        narrative_changes=tuple(narrative_changes),
        last_history_id=last_id,
    )
    return Replay(state, MappingProxyType(changes))


def band(value: int) -> int:
    """Returns the band of a value, 0 to 3 (docs/consistency-engine.md, From numbers to text)."""
    return sum(1 for start in BAND_STARTS[1:] if value >= start)


def one_line(text: str | None) -> str | None:
    """Returns `text` with every run of white space, line breaks included, made one space, or None when that leaves
    nothing or more than MAX_TEXT_CHARS characters. One line, so a proposed text cannot start a line of its own in a
    prompt."""
    if text is None:
        return None
    flat = " ".join(text.split())
    return flat if flat and len(flat) <= MAX_TEXT_CHARS else None


def requested_delta(event: RelationshipEvent, large_allowed: bool) -> int:
    """Returns the signed delta an event asks for: its magnitude's size, with `large` taken as `medium` unless
    `large_allowed` (an episode of importance LARGE_MIN_EPISODE_IMPORTANCE or more in the same run)."""
    magnitude = "medium" if event.magnitude == "large" and not large_allowed else event.magnitude
    size = MAGNITUDE_DELTAS[magnitude]
    return size if event.direction == "up" else -size


def relationship_proposal(event: RelationshipEvent, sources: Sequence[str], large_allowed: bool) -> Proposal | None:
    """Returns the row a relationship event asks for, citing `sources` (the ids of its window messages), or None when
    it has no source or its reason is blank or too long once on one line."""
    reason = one_line(event.reason)
    unique = tuple(dict.fromkeys(source for source in sources if source))
    if reason is None or not unique:
        return None
    return Proposal(
        RELATIONSHIP_CHANGE,
        reason,
        unique,
        dimension=event.dimension,
        delta=requested_delta(event, large_allowed),
    )


def _same(first: str, second: str, language: str | None) -> bool:
    # The deduplication test of memories: the same fingerprint, or a stem-set Jaccard of at least the threshold.
    if fingerprint(first, language) == fingerprint(second, language):
        return True
    return jaccard(stem_set(first, language), stem_set(second, language)) >= SIMILARITY_THRESHOLD


def _listed(text: str, items: Iterable[str], language: str | None) -> str | None:
    return next((item for item in items if _same(text, item, language)), None)


def update_proposals(
    state: RelationshipState, update: StateUpdate, sources: Sequence[str], language: str | None
) -> Planned:
    """Returns the text events a run's StateUpdate asks for against the current `state`, in this order: the emotional
    state, the closed threads, the new threads, the goals, the narrative change.

    Each cites `sources`, the ids of the run's window messages, because the update names no message of its own, and
    carries the fixed reason of its kind (UPDATE_REASONS). Texts are compared as memories are deduplicated: the same
    fingerprint or a stem Jaccard of at least SIMILARITY_THRESHOLD. An emotional state equal to the current one, a
    thread or goal already listed and a narrative change already recorded give no event. A close matching no open
    thread, a text that `one_line` refuses and the items of a list past MAX_ITEMS_PER_RUN are counted in `dropped`.
    `current_topic` has no event kind, so a given one is counted in `deferred` and not applied. Requires `sources` to
    name at least one message.
    """
    unique = tuple(dict.fromkeys(source for source in sources if source))
    proposals: list[Proposal] = []
    dropped = 0

    def propose(kind: str, *, before: str | None = None, after: str | None = None) -> None:
        proposals.append(Proposal(kind, UPDATE_REASONS[kind], unique, value_before=before, value_after=after))

    if update.emotional_state is not None:
        emotion = one_line(update.emotional_state)
        if emotion is None:
            dropped += 1
        elif state.emotional_state is None or not _same(emotion, state.emotional_state, language):
            propose(EMOTION_CHANGE, before=state.emotional_state, after=emotion)

    threads = list(state.open_threads)
    for index, raw in enumerate(update.open_threads_close):
        text = one_line(raw)
        thread = None if text is None or index >= MAX_ITEMS_PER_RUN else _listed(text, threads, language)
        if thread is None:
            dropped += 1
            continue
        threads.remove(thread)
        propose(THREAD_CLOSED, before=thread)

    for raw_items, kept, kind in (
        (update.open_threads_add, threads, THREAD_OPENED),
        (update.goals, list(state.goals), GOAL_CHANGE),
    ):
        added = 0
        for raw in raw_items:
            text = one_line(raw)
            if text is not None and _listed(text, kept, language) is not None:
                continue
            if text is None or added >= MAX_ITEMS_PER_RUN:
                dropped += 1
                continue
            kept.append(text)
            added += 1
            propose(kind, after=text)

    if update.narrative_change is not None:
        change = one_line(update.narrative_change)
        if change is None:
            dropped += 1
        elif _listed(change, state.narrative_changes, language) is None:
            propose(NARRATIVE_CHANGE, after=change)

    deferred = int(bool(update.current_topic and update.current_topic.strip()))
    return Planned(tuple(proposals), dropped, deferred)
