"""The Character State Manager's rules without a database: T-CON-06 (bounded deltas with a reason and sources), the pure
half of T-MEM-23 (random edit and delete sequences, checked against an independent reading of the rules), the text
events of a state update and block 4's words."""

import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

import pytest

from lyrveil.consistency.state import (
    DAY_LIMIT,
    DIMENSIONS,
    EMOTION_CHANGE,
    GOAL_CHANGE,
    INITIAL_VALUES,
    MAX_ITEMS_PER_RUN,
    NARRATIVE_CHANGE,
    RELATIONSHIP_CHANGE,
    RUN_LIMIT,
    THREAD_CLOSED,
    THREAD_OPENED,
    UPDATE_REASONS,
    USER_CORRECTION,
    Change,
    Proposal,
    RelationshipState,
    StateEvent,
    band,
    bounded,
    relationship_proposal,
    replay,
    requested_delta,
    update_proposals,
)
from lyrveil.context.blocks import state_lines
from lyrveil.context.minimal import UserProfile
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.domain.memory_extraction import RelationshipEvent, StateUpdate

ROME = ZoneInfo("Europe/Rome")
START = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)
MAGNITUDES = ("small", "medium", "large")


def change(row_id: str, dimension: str, delta: int, *, run: str | None = "r1", at: datetime = START) -> StateEvent:
    return StateEvent(
        id=row_id,
        event_kind=RELATIONSHIP_CHANGE,
        created_at=to_utc_text(at),
        run_id=run,
        dimension=dimension,
        delta=delta,
    )


def proposed(
    dimension: str = "trust", direction: str = "up", magnitude: str = "small", reason: str = "Si confida."
) -> RelationshipEvent:
    return RelationshipEvent.model_validate(
        {"dimension": dimension, "direction": direction, "magnitude": magnitude, "reason": reason, "source_seqs": [1]}
    )


def test_each_magnitude_has_its_size_and_large_needs_an_important_episode() -> None:
    """T-CON-06: per event."""
    assert [requested_delta(proposed(magnitude=m), large_allowed=True) for m in MAGNITUDES] == [2, 4, 8]
    assert [requested_delta(proposed(magnitude=m), large_allowed=False) for m in MAGNITUDES] == [2, 4, 4]
    assert requested_delta(proposed(direction="down", magnitude="large"), large_allowed=True) == -8
    assert requested_delta(proposed(direction="down", magnitude="large"), large_allowed=False) == -4


def test_one_run_moves_a_dimension_by_at_most_eight() -> None:
    """T-CON-06: per run. Each row keeps what it asked, and the fold records what it got."""
    rows = [
        change("a", "trust", 8),
        change("b", "trust", 4),
        change("c", "trust", -4),
        change("d", "trust", 8),
        change("e", "familiarity", 8),
        change("f", "trust", 2, run="r2"),
    ]

    result = replay(rows, UTC)

    assert dict(result.changes) == {
        "a": Change(20, 28),
        "b": Change(28, 28),
        "c": Change(28, 24),
        "d": Change(24, 28),
        "e": Change(10, 18),
        "f": Change(28, 30),
    }
    assert [row.delta for row in rows] == [8, 4, -4, 8, 8, 2], "the requested deltas stay as they were"
    assert (result.state.trust, result.state.familiarity, result.state.last_history_id) == (30, 18, "f")


def test_one_local_day_moves_a_dimension_by_at_most_fifteen() -> None:
    """T-CON-06: per local calendar day, in the deployment's zone."""
    rows = [
        change("a", "confidence", 8, run="r1", at=START),
        change("b", "confidence", 8, run="r2", at=START + timedelta(hours=4)),
        change("c", "confidence", 2, run="r3", at=START + timedelta(hours=10)),
        # 23:30 UTC on 7 November is 00:30 on 8 November in Rome.
        change("d", "confidence", 8, run="r4", at=START + timedelta(hours=15, minutes=30)),
        change("e", "confidence", -8, run="r5", at=START + timedelta(hours=16)),
    ]

    in_rome = replay(rows, ROME).changes
    in_utc = replay(rows, UTC).changes

    assert [in_rome[key].after - in_rome[key].before for key in "abcde"] == [8, 7, 0, 8, -8]
    assert [in_utc[key].after - in_utc[key].before for key in "abcde"] == [8, 7, 0, 0, -8]
    assert replay(rows, ROME).state.confidence == INITIAL_VALUES["confidence"] + 15
    assert replay(rows, UTC).state.confidence == INITIAL_VALUES["confidence"] + 7


def test_values_stay_between_zero_and_one_hundred() -> None:
    """T-CON-06: the range."""
    rows = [change("down", "tension", -2)]
    rows += [change(f"up{day}", "trust", 8, run=f"r{day}", at=START + timedelta(days=day)) for day in range(12)]

    result = replay(rows, UTC)

    assert result.changes["down"] == Change(0, 0)
    assert result.changes["up9"] == Change(92, 100)
    assert result.changes["up10"] == Change(100, 100)
    assert (result.state.tension, result.state.trust) == (0, 100)
    assert bounded(8, 97, 0, 0) == 3
    assert bounded(-8, 3, 0, 0) == -3
    assert bounded(4, 50, 6, 0) == 2
    assert bounded(-4, 50, -6, 0) == -2
    assert bounded(4, 50, 0, 14) == 1
    assert bounded(-4, 50, 0, -15) == 0


def test_every_change_needs_a_reason_and_its_sources() -> None:
    """T-CON-06: no row can be asked for without a reason and a source message."""
    good = Proposal(RELATIONSHIP_CHANGE, "Si confida.", ("m1",), dimension="trust", delta=2)
    assert good.sources == ("m1",)
    sources, dimension = "needs its source messages", "needs a dimension and a delta"
    refused: tuple[tuple[Callable[[], Proposal], str], ...] = (
        (lambda: Proposal(RELATIONSHIP_CHANGE, "  ", ("m1",), dimension="trust", delta=2), "needs a reason"),
        (lambda: Proposal(RELATIONSHIP_CHANGE, "Motivo.", (), dimension="trust", delta=2), sources),
        (lambda: Proposal(RELATIONSHIP_CHANGE, "Motivo.", ("",), dimension="trust", delta=2), sources),
        (lambda: Proposal(RELATIONSHIP_CHANGE, "Motivo.", ("m1",), dimension="trust", delta=3), "one magnitude"),
        (lambda: Proposal(RELATIONSHIP_CHANGE, "Motivo.", ("m1",), dimension="mood", delta=2), dimension),
        (lambda: Proposal(RELATIONSHIP_CHANGE, "Motivo.", ("m1",), dimension="trust"), dimension),
        (lambda: Proposal(EMOTION_CHANGE, "Motivo.", ("m1",), dimension="trust", delta=2), "only a relationship"),
        (lambda: Proposal(USER_CORRECTION, "Motivo.", ("m1",)), "cannot write a user_correction row"),
    )
    for make, message in refused:
        with pytest.raises(ValueError, match=message):
            make()

    assert relationship_proposal(proposed(reason="   "), ["m1"], large_allowed=False) is None
    assert relationship_proposal(proposed(), [], large_allowed=False) is None
    made = relationship_proposal(proposed(reason="Si\nconfida  con lei."), ["m2", "m1", "m2"], large_allowed=False)
    assert made is not None
    assert (made.reason, made.sources, made.dimension, made.delta) == ("Si confida con lei.", ("m2", "m1"), "trust", 2)


def _update(**fields: object) -> StateUpdate:
    return StateUpdate.model_validate(fields)


def test_a_state_update_becomes_text_events_against_the_current_state() -> None:
    state = RelationshipState(
        emotional_state="È stanca ma contenta.",
        open_threads=("Deve dirti come è andato il colloquio.", "Vuole sapere se hai finito il libro."),
        goals=("Vuole finire la mappa entro il mese.",),
        narrative_changes=("Ha lasciato la gilda dei cartografi.",),
    )
    update = _update(
        emotional_state="è stanca, ma contenta",
        current_topic="il colloquio",
        open_threads_add=["Vuole sapere se hai finito il libro", "Ti deve presentare la sorella.", "x" * 201],
        open_threads_close=["Deve dirti come è andato il colloquio", "Qualcosa che non c'è mai stato."],
        goals=["Vuole finire la mappa entro il mese", "Vuole imparare a nuotare."],
        narrative_change="Ha lasciato la gilda dei cartografi",
    )

    planned = update_proposals(state, update, ["m1", "m2", "m1"], "it")

    assert [(p.event_kind, p.value_before, p.value_after) for p in planned.proposals] == [
        (THREAD_CLOSED, "Deve dirti come è andato il colloquio.", None),
        (THREAD_OPENED, None, "Ti deve presentare la sorella."),
        (GOAL_CHANGE, None, "Vuole imparare a nuotare."),
    ]
    assert all(p.sources == ("m1", "m2") for p in planned.proposals), "the window's messages, each once"
    assert all(p.reason == UPDATE_REASONS[p.event_kind] for p in planned.proposals)
    assert (planned.dropped, planned.deferred) == (2, 1), "the unknown close and the long thread; the topic waits"

    other = _update(emotional_state="Di  ottimo\numore", narrative_change="Si è trasferita.")
    changed = update_proposals(state, other, ["m3"], "it")
    assert [(p.event_kind, p.value_before, p.value_after) for p in changed.proposals] == [
        (EMOTION_CHANGE, "È stanca ma contenta.", "Di ottimo umore"),
        (NARRATIVE_CHANGE, None, "Si è trasferita."),
    ]


def test_one_run_adds_at_most_four_items_to_a_list() -> None:
    threads = [
        "Deve raccontarti del viaggio a Lisbona.",
        "Vuole mostrarti le foto del matrimonio.",
        "Ti presenterà la sorella.",
        "Deve restituirti il libro.",
        "Vuole cucinare una torta.",
        "Ti spiegherà il nuovo lavoro.",
    ]

    planned = update_proposals(RelationshipState(), _update(open_threads_add=threads), ["m1"], "it")

    assert [p.value_after for p in planned.proposals] == threads[:MAX_ITEMS_PER_RUN]
    assert planned.dropped == len(threads) - MAX_ITEMS_PER_RUN


def test_the_fold_applies_text_events_and_ignores_unknown_closes() -> None:
    at = to_utc_text(START)
    rows = [
        StateEvent("e1", EMOTION_CHANGE, at, "r1", value_after="Allegra"),
        StateEvent("t1", THREAD_OPENED, at, "r1", value_after="Il colloquio"),
        StateEvent("t2", THREAD_OPENED, at, "r1", value_after="Il libro"),
        StateEvent("c1", THREAD_CLOSED, at, "r2", value_before="Il colloquio"),
        StateEvent("c2", THREAD_CLOSED, at, "r2", value_before="Mai aperto"),
        StateEvent("g1", GOAL_CHANGE, at, "r2", value_after="Finire la mappa"),
        StateEvent("n1", NARRATIVE_CHANGE, at, "r2", value_after="Ha cambiato casa"),
        StateEvent("u1", USER_CORRECTION, at, None, value_after="ignorata"),
    ]

    state = replay(rows, UTC).state

    assert state == RelationshipState(
        emotional_state="Allegra",
        open_threads=("Il libro",),
        goals=("Finire la mappa",),
        narrative_changes=("Ha cambiato casa",),
        last_history_id="u1",
    )


def _day(row: StateEvent, zone: tzinfo) -> date:
    return datetime.fromisoformat(row.created_at).astimezone(zone).date()


def _oracle(rows: Sequence[StateEvent], zone: tzinfo) -> dict[str, int]:
    """An independent reading of docs/consistency-engine.md (Update rules): every change moves its dimension one point
    at a time, and stops at the first point that would leave the range or take the run's or the day's net change past
    its limit."""
    values = dict(INITIAL_VALUES)
    by_run: dict[tuple[str, str], int] = defaultdict(int)
    by_day: dict[tuple[date, str], int] = defaultdict(int)
    for row in rows:
        if row.event_kind != RELATIONSHIP_CHANGE or row.dimension is None or row.delta is None:
            continue
        dimension = row.dimension
        run = (row.run_id or row.id, dimension)
        day = (_day(row, zone), dimension)
        step = 1 if row.delta > 0 else -1
        for _ in range(abs(row.delta)):
            value = values[dimension] + step
            if not 0 <= value <= 100 or abs(by_run[run] + step) > RUN_LIMIT or abs(by_day[day] + step) > DAY_LIMIT:
                break
            values[dimension] = value
            by_run[run] += step
            by_day[day] += step
    return values


def _random_run(
    rng: random.Random, number: int, at: datetime, cited: str | None
) -> list[tuple[StateEvent, frozenset[str]]]:
    """Returns the rows of one random run with their sources; `cited` is a message every row of the run cites, as a
    re-evaluation after an edit does."""
    rows: list[tuple[StateEvent, frozenset[str]]] = []
    for index in range(rng.randint(1, 6)):
        sources = {f"m{rng.randrange(12)}" for _ in range(rng.randint(1, 3))}
        if cited is not None:
            sources.add(cited)
        row_id = f"run{number}-{index}"
        if rng.random() < 0.75:
            size = rng.choice((2, 4, 8)) * rng.choice((1, -1))
            row = change(row_id, rng.choice(DIMENSIONS), size, run=f"run{number}", at=at)
        else:
            row = StateEvent(row_id, EMOTION_CHANGE, to_utc_text(at), f"run{number}", value_after=f"umore {number}")
        rows.append((row, frozenset(sources)))
    return rows


def test_random_edits_and_deletions_keep_the_state_equal_to_replaying_history() -> None:
    """T-MEM-23, pure half: runs, edits (invalidation, then a run that cites the edited message again) and deletions
    (invalidation only) in random order. After each step the replayed state of the rows that remain matches the
    independent reading of the rules, the limits hold for every run and local day, and no invalidated row leaves a
    trace."""
    for seed in range(300):
        rng = random.Random(seed)
        written: list[tuple[StateEvent, frozenset[str]]] = []
        invalidated: set[str] = set()
        moment = START
        for step in range(30):
            moment += timedelta(minutes=rng.choice((0, 30, 240, 600, 1500)))
            roll = rng.random()
            message = f"m{rng.randrange(12)}"
            if roll < 0.3:
                invalidated |= {row.id for row, sources in written if message in sources}
            if roll < 0.15 or roll >= 0.3:
                written += _random_run(rng, step, moment, message if roll < 0.15 else None)
            remaining = [row for row, _ in written if row.id not in invalidated]

            result = replay(remaining, ROME)

            values = {dimension: result.state.value(dimension) for dimension in DIMENSIONS}
            assert values == _oracle(remaining, ROME), f"seed {seed}, step {step}"
            assert set(result.changes) == {row.id for row in remaining if row.event_kind == RELATIONSHIP_CHANGE}
            emotions = [row.value_after for row in remaining if row.event_kind == EMOTION_CHANGE]
            assert result.state.emotional_state == (emotions[-1] if emotions else None)
            assert result.state.last_history_id == (remaining[-1].id if remaining else None)
            by_run: dict[tuple[str | None, str | None], int] = defaultdict(int)
            by_day: dict[tuple[date, str | None], int] = defaultdict(int)
            for row in remaining:
                if row.id in result.changes:
                    applied = result.changes[row.id].after - result.changes[row.id].before
                    assert row.delta is not None
                    assert abs(applied) <= abs(row.delta)
                    assert applied * row.delta >= 0
                    by_run[(row.run_id, row.dimension)] += applied
                    by_day[(_day(row, ROME), row.dimension)] += applied
            assert all(abs(total) <= RUN_LIMIT for total in by_run.values())
            assert all(abs(total) <= DAY_LIMIT for total in by_day.values())
            assert all(0 <= value <= 100 for value in values.values())
            fresh = replay([replace(row, id=f"fresh-{row.id}") for row in remaining], ROME).state
            assert {dimension: fresh.value(dimension) for dimension in DIMENSIONS} == values, "ids change nothing"


def test_block_four_words_the_state_without_numbers() -> None:
    sara = UserProfile(display_name="Sara")
    first, no_details = state_lines(RelationshipState(), sara)
    assert first == (
        "Il vostro rapporto: vi siete appena conosciuti, sei prudente con Sara, parli poco di te, nessuna tensione.",
    )
    assert no_details == ()

    state = RelationshipState(
        familiarity=45,
        trust=19,
        confidence=70,
        tension=25,
        emotional_state="È di buon umore",
        open_threads=("Deve dirti come è andato il colloquio.", "Vuole sapere del libro"),
    )
    lines, details = state_lines(state, sara)

    assert lines == (
        "Il vostro rapporto: vi conoscete bene, diffidi di Sara, con Sara parli di tutto, qualche attrito.",
        "Come ti senti ora: È di buon umore.",
    )
    assert details == ("Discorsi lasciati in sospeso: Deve dirti come è andato il colloquio; Vuole sapere del libro.",)
    assert not any(character.isdigit() for character in "".join(lines + details)), "numbers never reach the prompt"
    assert [band(value) for value in (0, 19, 20, 39, 40, 69, 70, 100)] == [0, 0, 1, 1, 2, 2, 3, 3]
