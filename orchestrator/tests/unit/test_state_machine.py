"""T-ORC-01: every legal transition of the GPU state machine works and every other one is refused."""

import ast
import itertools
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lyrveil_orchestrator import state_machine
from lyrveil_orchestrator.state_machine import (
    INITIAL_STATE,
    STATE_RUNTIME,
    TRANSITIONS,
    GpuState,
    IllegalTransition,
    allowed_targets,
    is_legal,
    transition,
)

S = GpuState

# Written out again from the diagram of docs/gpu-orchestration.md, so that a change to the table must change this too.
EXPECTED_STATES = {
    "IDLE",
    "LOADING_LLM",
    "LLM_READY",
    "GENERATING_TEXT",
    "UNLOADING_LLM",
    "LOADING_IMAGE",
    "IMAGE_READY",
    "GENERATING_IMAGE",
    "UNLOADING_IMAGE",
    "RECOVERING",
    "ERROR",
}
EXPECTED_EDGES = {
    (S.RECOVERING, S.IDLE),
    (S.RECOVERING, S.LLM_READY),
    (S.RECOVERING, S.IMAGE_READY),
    (S.RECOVERING, S.ERROR),
    (S.IDLE, S.LOADING_LLM),
    (S.IDLE, S.LOADING_IMAGE),
    (S.LOADING_LLM, S.LLM_READY),
    (S.LOADING_LLM, S.RECOVERING),
    (S.LLM_READY, S.GENERATING_TEXT),
    (S.LLM_READY, S.UNLOADING_LLM),
    (S.LLM_READY, S.RECOVERING),
    (S.GENERATING_TEXT, S.LLM_READY),
    (S.GENERATING_TEXT, S.RECOVERING),
    (S.UNLOADING_LLM, S.IDLE),
    (S.UNLOADING_LLM, S.RECOVERING),
    (S.LOADING_IMAGE, S.IMAGE_READY),
    (S.LOADING_IMAGE, S.RECOVERING),
    (S.IMAGE_READY, S.GENERATING_IMAGE),
    (S.IMAGE_READY, S.UNLOADING_IMAGE),
    (S.IMAGE_READY, S.RECOVERING),
    (S.GENERATING_IMAGE, S.IMAGE_READY),
    (S.GENERATING_IMAGE, S.RECOVERING),
    (S.UNLOADING_IMAGE, S.IDLE),
    (S.UNLOADING_IMAGE, S.RECOVERING),
    (S.ERROR, S.RECOVERING),
}
ALL_PAIRS = list(itertools.product(GpuState, repeat=2))


def test_the_table_names_every_documented_state_and_nothing_else() -> None:
    assert {state.value for state in GpuState} == EXPECTED_STATES
    assert set(STATE_RUNTIME) == set(GpuState)
    assert {t.source for t in TRANSITIONS} == set(GpuState)
    assert {t.target for t in TRANSITIONS} | {INITIAL_STATE} == set(GpuState)


def test_the_table_holds_exactly_the_documented_edges_once_each() -> None:
    edges = [(t.source, t.target) for t in TRANSITIONS]
    assert len(edges) == len(set(edges))
    assert set(edges) == EXPECTED_EDGES
    assert all(t.trigger for t in TRANSITIONS)


def test_the_process_starts_in_recovering() -> None:
    assert INITIAL_STATE is GpuState.RECOVERING


@pytest.mark.parametrize(("source", "target"), ALL_PAIRS)
def test_a_move_is_accepted_exactly_when_the_diagram_has_it(source: GpuState, target: GpuState) -> None:
    if (source, target) in EXPECTED_EDGES:
        assert is_legal(source, target)
        assert transition(source, target) is target
        assert target in allowed_targets(source)
    else:
        assert not is_legal(source, target)
        assert target not in allowed_targets(source)
        with pytest.raises(IllegalTransition) as raised:
            transition(source, target)
        assert (raised.value.source, raised.value.target) == (source, target)
        assert f"{source} -> {target}" in str(raised.value)


def test_loading_is_entered_only_from_idle() -> None:
    loading = {S.LOADING_LLM, S.LOADING_IMAGE}
    assert {t.source for t in TRANSITIONS if t.target in loading} == {S.IDLE}


def test_idle_is_entered_only_after_a_release_check() -> None:
    # The two unloading states verify the release; RECOVERING enters IDLE only when nothing runs.
    assert {t.source for t in TRANSITIONS if t.target is S.IDLE} == {S.UNLOADING_LLM, S.UNLOADING_IMAGE, S.RECOVERING}


def test_no_move_hands_the_gpu_from_one_runtime_to_the_other() -> None:
    for t in TRANSITIONS:
        runtimes = {STATE_RUNTIME[t.source], STATE_RUNTIME[t.target]} - {None}
        assert len(runtimes) <= 1, t


@settings(database=None, max_examples=300)
@given(st.lists(st.sampled_from(list(GpuState)), max_size=60))
def test_random_sequences_apply_legal_moves_and_refuse_the_rest(targets: list[GpuState]) -> None:
    state = INITIAL_STATE
    for target in targets:
        if (state, target) in EXPECTED_EDGES:
            state = transition(state, target)
            assert state is target
        else:
            with pytest.raises(IllegalTransition):
                transition(state, target)
        assert allowed_targets(state), f"{state} is a dead end"


@settings(database=None, max_examples=300)
@given(st.data())
def test_random_legal_walks_change_runtime_only_through_a_state_without_one(data: st.DataObject) -> None:
    state = INITIAL_STATE
    last_runtime = STATE_RUNTIME[state]
    for _ in range(80):
        state = transition(state, data.draw(st.sampled_from(sorted(allowed_targets(state)))))
        runtime = STATE_RUNTIME[state]
        assert last_runtime is None or runtime is None or runtime is last_runtime
        last_runtime = runtime


# The standard library modules the state machine may import. None of them does I/O on import or through what the
# module uses from it.
PURE_IMPORTS = {"collections.abc", "dataclasses", "enum", "types"}
IO_NAMES = {"open", "print", "input", "exec", "eval", "__import__"}


def test_the_state_machine_module_performs_no_io() -> None:
    source = Path(state_machine.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports are not expected"
            imported.add(node.module or "")
    assert imported <= PURE_IMPORTS
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert called.isdisjoint(IO_NAMES)
