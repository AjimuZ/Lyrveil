"""Tests of tests/fixtures/tokenization: the prompts T-GPU-03 tokenizes with the real models are the formatters' output.

The gpu level (tests/gpu/TextProfiles.Tests.ps1 in the repository root) tokenizes the chat prompt of each family in
prompts.json with a real model and records the identifiers in <family>.tokens.json, with the SHA-256 of the prompt it
tokenized. These tests tie that recording to lyrveil/prompts/formatter.py: a formatter change that the recording did not
see, or a recording of another prompt, fails here without a GPU.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, get_args

import pytest

from lyrveil.catalog import TextTemplate
from lyrveil.prompts.formatter import ChatParts, Turn, formatter_for

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tokenization"
FAMILIES: tuple[TextTemplate, ...] = get_args(TextTemplate)


def _load(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded


PROMPTS = _load("prompts.json")


def _parts() -> ChatParts:
    raw = PROMPTS["parts"]
    return ChatParts(
        stable_text=raw["stable_text"],
        history=tuple(Turn(turn["role"], turn["text"]) for turn in raw["history"]),
        state=raw["state"],
        memories=raw["memories"],
        anchor=raw["anchor"],
        character_first_name=raw["character_first_name"],
        user_display_name=raw["user_display_name"],
    )


def test_every_catalog_family_has_a_fixture_prompt() -> None:
    assert sorted(PROMPTS["families"]) == sorted(FAMILIES)


@pytest.mark.parametrize("family", FAMILIES)
def test_the_fixture_prompts_are_what_the_formatter_renders(family: TextTemplate) -> None:
    fixture = PROMPTS["families"][family]
    formatter = formatter_for(family)
    structured = PROMPTS["structured"]

    assert formatter.chat(_parts(), PROMPTS["current_message"]).text == fixture["prompt"]
    assert formatter.structured(structured["system_text"], structured["user_text"]) == fixture["structured_prompt"]
    assert tuple(fixture["stop_sequences"]) == formatter.stop_sequences(PROMPTS["parts"]["user_display_name"])
    assert tuple(fixture["control_texts"]) == formatter.control_texts()


@pytest.mark.parametrize("family", FAMILIES)
def test_the_recorded_tokenization_is_of_the_fixture_prompt(family: TextTemplate) -> None:
    recorded = _load(f"{family}.tokens.json")
    prompt = PROMPTS["families"][family]["prompt"]

    assert recorded["family"] == family
    assert recorded["special"] is False
    assert recorded["prompt_sha256"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert recorded["count"] == len(recorded["ids"]) > 0
    assert all(isinstance(token, int) and token >= 0 for token in recorded["ids"])
