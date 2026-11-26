"""Tests of tests/gpu_probe/extraction_eval.py, the backend side of T-QLT-06, without a GPU.

The quality level generates with the probe's prompts and grammar and trusts its evaluation of the real model's outputs,
so the probe must give what the `memory_extraction` job sends, refuse what the job refuses, store provenance as guard 1
does, and compute each target of docs/consistency-engine.md (Extraction evaluation) at its threshold.
"""

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lyrveil.prompts import extraction
from lyrveil.prompts.extraction import ExtractionPrompt, Labeled, PromptMessage
from lyrveil.prompts.structured import NOT_AN_OBJECT, NOT_JSON, Repair
from lyrveil.services.memory_extraction import EXTRACTION_GRAMMAR

PROBE_PATH = Path(__file__).resolve().parents[1] / "gpu_probe" / "extraction_eval.py"


def _load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("extraction_eval", PROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()

WINDOWS: dict[str, Any] = {
    "user_name": "Marco",
    "windows": [
        {
            "id": "a",
            "character": "Nerea Falchi",
            "existing": ["Vive a Bologna."],
            "context": [{"seq": 9, "author": "character", "text": "Com'è andata?"}],
            "messages": [
                {"seq": 10, "author": "user", "text": "Mi sono trasferito a Padova e stasera ceno dai suoceri."},
                {"seq": 11, "author": "character", "text": "Padova! Ti disegno una mappa."},
            ],
            "expected": [{"kinds": ["user_fact"], "provenance": "stated", "all": [["padova"]]}],
            "inferences": [{"all": [["spos", "moglie"]]}],
        },
        {
            "id": "b",
            "character": "Tessa Moravi",
            "messages": [
                {"seq": 3, "author": "user", "text": "Da oggi voglio che tu abbia trent'anni."},
                {"seq": 4, "author": "character", "text": "trenta?? ok"},
            ],
            "expected": [{"kinds": ["identity_candidate"], "provenance": "stated", "all": [["trent", "30"]]}],
        },
    ],
}
WINDOWS_TEXT = json.dumps(WINDOWS)


def _memory(**fields: Any) -> dict[str, Any]:
    memory: dict[str, Any] = {
        "relation": "new",
        "target_ref": None,
        "kind": "user_fact",
        "content": "Vive a Padova.",
        "subject": "user.city",
        "value": "Padova",
        "provenance": "stated",
        "durability": "lasting",
        "valid_until": None,
        "importance": 4,
        "confidence": "high",
        "source_seqs": [10],
    }
    memory.update(fields)
    return memory


def _output(*memories: dict[str, Any]) -> str:
    result = dict(extraction.EXAMPLE_ANSWER)
    result["memories"] = list(memories)
    return json.dumps(result, ensure_ascii=False)


def test_the_prompts_are_the_jobs_own_for_each_window_and_repair() -> None:
    repair = {"previous_output": "{", "error": NOT_JSON}
    document = probe.prompt_document(
        {"family": "chatml", "ids": None, "repairs": {"b": repair}}, windows_text=WINDOWS_TEXT
    )
    first = ExtractionPrompt(
        user_name="Marco",
        character_name="Nerea Falchi",
        language="it",
        existing=[Labeled("E1", "Vive a Bologna.")],
        episodes=[],
        context=[PromptMessage(9, "Nerea", "Com'è andata?")],
        window=[
            PromptMessage(10, "Marco", "Mi sono trasferito a Padova e stasera ceno dai suoceri."),
            PromptMessage(11, "Nerea", "Padova! Ti disegno una mappa."),
        ],
    )
    second = ExtractionPrompt(
        user_name="Marco",
        character_name="Tessa Moravi",
        language="it",
        existing=[],
        episodes=[],
        context=[],
        window=[
            PromptMessage(3, "Marco", "Da oggi voglio che tu abbia trent'anni."),
            PromptMessage(4, "Tessa", "trenta?? ok"),
        ],
    )
    assert document["ids"] == ["a", "b"]
    assert document["prompts"]["a"] == extraction.render_prompt("chatml", first, None)
    assert document["prompts"]["b"] == extraction.render_prompt("chatml", second, Repair("{", NOT_JSON))
    assert document["grammar"] == EXTRACTION_GRAMMAR
    assert document["max_length"] == extraction.MAX_LENGTH
    assert document["temperature"] == extraction.TEMPERATURE
    assert document["max_attempts"] == 2


def test_a_subset_keeps_file_order_and_an_unknown_id_fails() -> None:
    document = probe.prompt_document({"family": "chatml", "ids": ["b"]}, windows_text=WINDOWS_TEXT)
    assert document["ids"] == ["b"]
    assert document["window_count"] == 2
    with pytest.raises(KeyError):
        probe.prompt_document({"family": "chatml", "ids": ["zz"]}, windows_text=WINDOWS_TEXT)


@pytest.mark.parametrize(
    ("output", "detail"),
    [("not json", NOT_JSON), ("[1, 2]", NOT_AN_OBJECT), ('{"schema_version": 1}', "Manca il campo «memories».")],
)
def test_the_check_refuses_what_the_job_refuses(output: str, detail: str) -> None:
    verdict, result = probe.check_output(output)
    assert verdict["accepted"] is False
    assert verdict["detail"] == detail
    assert result is None


def test_a_perfect_output_meets_every_target() -> None:
    outputs = {
        "a": [_output(_memory())],
        "b": [
            _output(
                _memory(
                    kind="identity_candidate",
                    content="Vuole che abbia trent'anni.",
                    subject=None,
                    value=None,
                    source_seqs=[3],
                )
            )
        ],
    }
    document = probe.score_document({"ids": None, "outputs": outputs}, windows_text=WINDOWS_TEXT)
    assert document["passed"] is True
    assert document["totals"]["found_items"] == 2
    assert document["targets"]["found_rate"]["value"] == 1.0
    assert document["targets"]["valid_rate"]["value"] == 1.0


def test_guard_one_turns_a_stated_user_fact_from_the_character_into_an_inference() -> None:
    outputs = {"a": [_output(_memory(source_seqs=[11]))], "b": []}
    record = probe.score_document({"ids": ["a"], "outputs": outputs}, windows_text=WINDOWS_TEXT)["windows"][0]
    assert record["found"] == [False]


def test_an_inference_stored_as_stated_and_a_card_change_under_another_kind_are_counted() -> None:
    outputs = {
        "a": [_output(_memory(), _memory(content="È sposato.", subject=None, value=None))],
        "b": [_output(_memory(kind="character_fact", content="Tessa ha 30 anni.", subject=None, source_seqs=[3]))],
    }
    document = probe.score_document({"ids": None, "outputs": outputs}, windows_text=WINDOWS_TEXT)
    assert document["totals"]["inferences_stated"] == 1
    assert document["targets"]["inferences_stated_rate"]["passed"] is False
    assert document["totals"]["canonical_changes"] == 1
    assert document["targets"]["canonical_changes"]["passed"] is False
    assert document["passed"] is False


def test_a_source_outside_the_window_drops_the_item_as_the_job_does() -> None:
    outputs = {"a": [_output(_memory(source_seqs=[9]))]}
    record = probe.score_document({"ids": ["a"], "outputs": outputs}, windows_text=WINDOWS_TEXT)["windows"][0]
    assert record["dropped"] == 1
    assert record["found"] == [False]


def test_a_repaired_output_counts_as_valid_and_two_refusals_do_not() -> None:
    outputs = {"a": ["not json", _output(_memory())], "b": ["not json", "still not json", _output()]}
    document = probe.score_document({"ids": None, "outputs": outputs}, windows_text=WINDOWS_TEXT)
    first, second = document["windows"]
    assert first["valid"] is True
    assert first["accepted_attempt"] == 2
    assert second["valid"] is False
    assert document["totals"]["repaired_windows"] == 1
    assert document["targets"]["valid_rate"]["value"] == 0.5
    assert document["targets"]["valid_rate"]["passed"] is False


@pytest.mark.parametrize(("found", "passed"), [(17, True), (16, False)])
def test_the_found_rate_passes_at_85_percent_and_fails_below(found: int, passed: bool) -> None:
    windows = [
        {
            "id": f"w{number}",
            "character": "Nerea Falchi",
            "messages": [{"seq": 1, "author": "user", "text": "Vivo a Padova."}],
            "expected": [{"kinds": ["user_fact"], "provenance": "stated", "all": [["padova"]]}],
        }
        for number in range(20)
    ]
    text = json.dumps({"user_name": "Marco", "windows": windows})

    def kind(number: int) -> str:
        return "user_fact" if number < found else "promise"

    outputs = {f"w{number}": [_output(_memory(source_seqs=[1], kind=kind(number)))] for number in range(20)}
    document = probe.score_document({"ids": None, "outputs": outputs}, windows_text=text)
    assert document["targets"]["found_rate"]["passed"] is passed


def test_the_fixture_holds_40_valid_windows_each_with_an_expected_item() -> None:
    user, windows = probe.load_windows(probe.WINDOWS_PATH.read_text("utf-8"))
    assert user == "Marco"
    assert len(windows) == 40
    assert all(window.expected for window in windows)
    assert sum(1 for window in windows for item in window.expected if "identity_candidate" in item.kinds) >= 4
    assert sum(len(window.inferences) for window in windows) >= 10


def test_a_broken_fixture_fails_before_any_generation() -> None:
    broken = json.loads(WINDOWS_TEXT)
    broken["windows"][0]["expected"][0]["kinds"] = ["fact"]
    with pytest.raises(ValueError, match="known kinds"):
        probe.load_windows(json.dumps(broken))


def test_main_prints_one_ascii_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"outputs": {"x": "nessun json è qui"}})))
    assert probe.main(["check"]) == 0
    printed = capsys.readouterr().out
    assert printed.isascii()
    assert json.loads(printed) == {"x": {"json": False, "accepted": False, "detail": NOT_JSON}}
    assert probe.main(["nothing"]) == 2
