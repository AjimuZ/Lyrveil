"""Tests of tests/gpu_probe/structured_task.py, the backend side of T-GPU-05, without a GPU.

The gpu level generates with the probe's prompt and grammar and trusts its verdict on the real model's outputs, so the
probe must give what the `character_draft` job sends and judge as the job's validation does.
"""

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lyrveil.prompts import character_draft
from lyrveil.prompts.structured import NOT_AN_OBJECT, NOT_JSON, Repair
from lyrveil.services.character_creator import DRAFT_GRAMMAR

PROBE_PATH = Path(__file__).resolve().parents[1] / "gpu_probe" / "structured_task.py"
DESCRIPTION = "Una libraia di 41 anni, ironica e riservata."


def _load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("structured_task", PROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def _example(**identity: object) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(json.dumps(character_draft.EXAMPLE_DRAFT))
    raw["identity"].update(identity)
    return raw


def test_the_prompt_and_grammar_are_what_the_draft_job_sends() -> None:
    document = probe.prompt_document({"family": "mistral_v3_tekken", "description": DESCRIPTION, "repair": None})

    assert document["prompt"] == character_draft.render_prompt("mistral_v3_tekken", DESCRIPTION, None, None)
    assert document["grammar"] == DRAFT_GRAMMAR
    assert document["draft_keys"] == ["identity", "traits", "communication", "visual"]
    assert (document["max_length"], document["temperature"], document["max_attempts"]) == (
        character_draft.MAX_LENGTH,
        character_draft.TEMPERATURE,
        2,
    )


def test_the_repair_prompt_carries_the_refused_output_and_its_error() -> None:
    repair = {"previous_output": "{", "error": NOT_JSON}
    document = probe.prompt_document({"family": "chatml", "description": DESCRIPTION, "repair": repair})

    assert document["prompt"] == character_draft.render_prompt("chatml", DESCRIPTION, None, Repair("{", NOT_JSON))


def test_a_valid_draft_is_accepted_as_a_card() -> None:
    document = probe.check_document(json.dumps(_example(), ensure_ascii=False))

    assert document["accepted"] is True
    assert (document["json"], document["blocked"], document["age"]) == (True, False, 34)
    assert document["keys"] == ["identity", "traits", "communication", "visual"]
    assert document["proposed_fields"] > 2


@pytest.mark.parametrize(
    ("output", "detail"),
    [("non è JSON", NOT_JSON), ("[1, 2]", NOT_AN_OBJECT)],
)
def test_an_output_that_is_not_a_json_object_is_refused(output: str, detail: str) -> None:
    document = probe.check_document(output)

    assert (document["json"], document["accepted"], document["detail"]) == (False, False, detail)


def test_an_underage_or_blocked_draft_is_refused_with_the_jobs_reason() -> None:
    underage = probe.check_document(json.dumps(_example(age=17)))
    blocked = probe.check_document(json.dumps(_example(biography="Una ragazzina timida."), ensure_ascii=False))

    assert (underage["json"], underage["accepted"], underage["blocked"]) == (True, False, False)
    assert underage["detail"]
    assert (blocked["accepted"], blocked["blocked"]) == (False, True)
    assert "identity.biography" in blocked["detail"]


def test_main_prints_one_ascii_json_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"output": json.dumps(_example(), ensure_ascii=False)})))

    assert probe.main(["check"]) == 0
    printed = capsys.readouterr().out
    assert printed.isascii()
    assert json.loads(printed)["accepted"] is True
    assert probe.main(["generate"]) == 2
