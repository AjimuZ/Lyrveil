"""The backend side of T-QLT-06: the memory extraction job's real prompt and grammar for synthetic windows, and the
evaluation of the real model's outputs against the items each window expects (docs/consistency-engine.md, Extraction
evaluation).

tests/quality/Extraction.Tests.ps1 (repository root) runs this file in a container of the backend's test stage, with no
network, and sends the outputs of the real text model through it. So the quality level generates with exactly what the
`memory_extraction` job sends (`extraction.render_prompt`, `EXTRACTION_GRAMMAR`, its length and temperature), refuses
exactly what the job's validation refuses (`validate_result`), and stores provenance as guard 1 does
(`checked_provenance`):

    python tests/gpu_probe/extraction_eval.py prompt < {"family": ..., "ids": [...] | null, "repairs": {id: repair}}
    python tests/gpu_probe/extraction_eval.py check < {"outputs": {id: text}}
    python tests/gpu_probe/extraction_eval.py score < {"ids": [...] | null, "outputs": {id: [text, ...]}}

The windows are WINDOWS_PATH, 40 synthetic exchanges with original characters and text. Each action reads one JSON
object from standard input and prints one JSON line, ASCII only, so Windows PowerShell 5.1 reads it byte for byte.
Nothing here is logged.
"""

import hashlib
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lyrveil.catalog import TextTemplate
from lyrveil.domain.memory_extraction import TARGETED_RELATIONS, ExtractionResult
from lyrveil.memory.consistency import checked_provenance, subject_key
from lyrveil.prompts import extraction
from lyrveil.prompts.extraction import ExtractionPrompt, Labeled, PromptMessage
from lyrveil.prompts.structured import MAX_ATTEMPTS, NOT_AN_OBJECT, NOT_JSON, OutputRejectedError, Repair
from lyrveil.runtimes.koboldcpp import TOTAL_TIMEOUT_S
from lyrveil.services.memory_extraction import EXTRACTION_GRAMMAR, PROMPT_VERSION, validate_result

USAGE = "usage: extraction_eval.py prompt|check|score < request.json"
WINDOWS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "extraction_eval" / "windows.json"
IDENTITY_CANDIDATE = "identity_candidate"
KINDS = frozenset(
    {
        "user_fact",
        "user_preference",
        "character_fact",
        "narrative_event",
        "promise",
        "decision",
        "open_thread",
        "boundary",
        IDENTITY_CANDIDATE,
    }
)
PROVENANCES = frozenset({"stated", "inferred", "fictional"})
# docs/consistency-engine.md (Extraction evaluation): the four targets, each with the comparison it must meet.
TARGETS: dict[str, tuple[str, float]] = {
    "found_rate": (">=", 0.85),
    "inferences_stated_rate": ("<=", 0.03),
    "canonical_changes": ("<=", 0),
    "valid_rate": (">=", 0.98),
}


@dataclass(frozen=True)
class Expected:
    """An item a window should yield: one of `kinds` (the first is the right one, the others equally right for an
    ambiguous item), `provenance` as stored after guard 1, and `groups`, keyword groups that must all match the stored
    text, each by any of its folded fragments."""

    kinds: tuple[str, ...]
    provenance: str
    groups: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Window:
    """One synthetic window: the character's full name, the existing memories the prompt shows as E1..En, the context
    messages and the analyzed messages as (seq, author, text) with author `user` or `character`, the expected items,
    the inferences (keyword groups of what the window only implies, which must never be stored `stated`)."""

    id: str
    character: str
    existing: tuple[str, ...]
    context: tuple[tuple[int, str, str], ...]
    messages: tuple[tuple[int, str, str], ...]
    expected: tuple[Expected, ...]
    inferences: tuple[tuple[tuple[str, ...], ...], ...]


def fold(text: str) -> str:
    """Returns `text` lowercase, without accents, with every apostrophe as `'` and runs of whitespace as one space, the
    form keyword fragments are written in."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").replace("`", "'").split())


def _groups(raw: Any, where: str) -> tuple[tuple[str, ...], ...]:
    """Returns the keyword groups of `raw`, a non-empty list of non-empty lists of fragments, each folded. Raises
    ValueError naming `where` otherwise."""
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{where}: 'all' must be a non-empty list of keyword groups")
    groups: list[tuple[str, ...]] = []
    for group in raw:
        if (
            not isinstance(group, list)
            or not group
            or not all(isinstance(part, str) and part.strip() for part in group)
        ):
            raise ValueError(f"{where}: every keyword group must be a non-empty list of fragments")
        groups.append(tuple(fold(part) for part in group))
    return tuple(groups)


def _messages(raw: Any, where: str) -> tuple[tuple[int, str, str], ...]:
    if not isinstance(raw, list):
        raise ValueError(f"{where}: messages must be a list")
    messages: list[tuple[int, str, str]] = []
    for item in raw:
        seq, author, text = item.get("seq"), item.get("author"), item.get("text")
        if type(seq) is not int or author not in ("user", "character") or not isinstance(text, str) or not text.strip():
            raise ValueError(f"{where}: every message needs an integer seq, author user or character, and text")
        if messages and seq <= messages[-1][0]:
            raise ValueError(f"{where}: message seqs must increase")
        messages.append((seq, author, text))
    return tuple(messages)


def load_windows(text: str) -> tuple[str, list[Window]]:
    """Returns the user's name and the windows of a windows document, in file order.

    Raises ValueError, naming the window, for a duplicate id, an empty window, seqs that do not increase across the
    context and the window, an unknown kind or provenance, or an empty keyword group, so a broken fixture fails before
    any generation."""
    document = json.loads(text)
    user = document.get("user_name")
    if not isinstance(user, str) or not user.strip():
        raise ValueError("the windows document needs a user_name")
    windows: list[Window] = []
    seen: set[str] = set()
    for raw in document.get("windows", []):
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError(f"window {identifier!r}: the id must be a new non-empty text")
        seen.add(identifier)
        context = _messages(raw.get("context", []), identifier)
        messages = _messages(raw.get("messages"), identifier)
        if not messages:
            raise ValueError(f"{identifier}: a window needs at least one message")
        if context and context[-1][0] >= messages[0][0]:
            raise ValueError(f"{identifier}: the context must come before the window")
        expected: list[Expected] = []
        for item in raw.get("expected", []):
            kinds = item.get("kinds")
            if not isinstance(kinds, list) or not kinds or any(kind not in KINDS for kind in kinds):
                raise ValueError(f"{identifier}: every expected item needs known kinds")
            if item.get("provenance") not in PROVENANCES:
                raise ValueError(f"{identifier}: every expected item needs a known provenance")
            expected.append(Expected(tuple(kinds), item["provenance"], _groups(item.get("all"), identifier)))
        inferences = tuple(_groups(item.get("all"), identifier) for item in raw.get("inferences", []))
        existing = raw.get("existing", [])
        if not isinstance(existing, list) or not all(isinstance(entry, str) and entry.strip() for entry in existing):
            raise ValueError(f"{identifier}: existing memories must be texts")
        character = raw.get("character")
        if not isinstance(character, str) or not character.strip():
            raise ValueError(f"{identifier}: the window needs a character")
        windows.append(Window(identifier, character, tuple(existing), context, messages, tuple(expected), inferences))
    if not windows:
        raise ValueError("the windows document holds no window")
    return user, windows


def _selected(windows: list[Window], ids: Any) -> list[Window]:
    """Returns the windows named by `ids` in file order, or all of them when `ids` is None. Raises KeyError for an id
    that names no window."""
    if ids is None:
        return windows
    known = {window.id for window in windows}
    unknown = [identifier for identifier in ids if identifier not in known]
    if unknown:
        raise KeyError(f"no window {unknown[0]!r}")
    wanted = set(ids)
    return [window for window in windows if window.id in wanted]


def _prompt_data(user: str, window: Window) -> ExtractionPrompt:
    """Returns what the job's prompt shows for `window`, labeled as MemoryExtractionService labels it: E1..En for the
    existing memories, the user's name and the character's first name as authors."""
    first = window.character.split()[0]
    names = {"user": user, "character": first}
    return ExtractionPrompt(
        user_name=user,
        character_name=window.character,
        language="it",
        existing=[Labeled(f"E{number}", text) for number, text in enumerate(window.existing, 1)],
        episodes=[],
        context=[PromptMessage(seq, names[author], text) for seq, author, text in window.context],
        window=[PromptMessage(seq, names[author], text) for seq, author, text in window.messages],
    )


def prompt_document(request: dict[str, Any], windows_text: str | None = None) -> dict[str, Any]:
    """Returns the generations of the windows `ids` (all when null) in the format of `family`: `ids` in order and
    `prompts` by id, each with the repair of `repairs[id]` (`previous_output`, `error`) when given, plus the grammar and
    the other values the job sends and the bounds it applies. Raises KeyError for a missing family or an unknown id,
    and ValueError for a broken windows document."""
    family: TextTemplate = request["family"]
    user, windows = load_windows(windows_text if windows_text is not None else WINDOWS_PATH.read_text("utf-8"))
    chosen = _selected(windows, request.get("ids"))
    repairs = request.get("repairs") or {}
    prompts: dict[str, str] = {}
    for window in chosen:
        raw = repairs.get(window.id)
        repair = None if raw is None else Repair(str(raw["previous_output"]), str(raw["error"]))
        prompts[window.id] = extraction.render_prompt(family, _prompt_data(user, window), repair)
    return {
        "task": "memory_extraction",
        "prompt_version": PROMPT_VERSION,
        "family": family,
        "ids": [window.id for window in chosen],
        "window_count": len(windows),
        "prompts": prompts,
        "grammar": EXTRACTION_GRAMMAR,
        "grammar_sha256": hashlib.sha256(EXTRACTION_GRAMMAR.encode("utf-8")).hexdigest(),
        "max_length": extraction.MAX_LENGTH,
        "temperature": extraction.TEMPERATURE,
        "max_attempts": MAX_ATTEMPTS,
        "total_timeout_s": TOTAL_TIMEOUT_S,
    }


def check_output(output: str) -> tuple[dict[str, Any], ExtractionResult | None]:
    """Returns how the job judges `output` (`json`: a JSON object; `accepted`; `detail`: the Italian reason of a
    refusal, the text a repair prompt carries) and the accepted result, or None."""
    verdict: dict[str, Any] = {"json": False, "accepted": False, "detail": ""}
    try:
        parsed = json.loads(output)
    except ValueError:
        verdict["detail"] = NOT_JSON
        return verdict, None
    if not isinstance(parsed, dict):
        verdict["detail"] = NOT_AN_OBJECT
        return verdict, None
    verdict["json"] = True
    try:
        result = validate_result(parsed)
    except OutputRejectedError as refused:
        verdict["detail"] = refused.detail
        return verdict, None
    verdict["accepted"] = True
    return verdict, result


def stored_items(window: Window, result: ExtractionResult) -> list[dict[str, Any]]:
    """Returns the memory items of `result` that the job would hand to the consistency rules, each with its kind, the
    provenance guard 1 stores, and its folded text (content and value).

    Like MemoryExtractionService, it drops an item with a source outside the window, an unknown `target_ref`, a
    relation that needs a target without one, or blank content; `dropped` counts them in the caller."""
    authors = {seq: author for seq, author, _ in window.messages}
    refs = {f"E{number}" for number in range(1, len(window.existing) + 1)}
    items: list[dict[str, Any]] = []
    for item in result.memories:
        seqs = set(item.source_seqs)
        unknown_target = item.target_ref is not None and item.target_ref not in refs
        missing_target = item.relation in TARGETED_RELATIONS and item.target_ref not in refs
        if any(seq not in authors for seq in seqs) or unknown_target or missing_target or not item.content.strip():
            items.append({"dropped": True, "kind": item.kind})
            continue
        provenance = checked_provenance(
            item.provenance,
            item.kind,
            subject_key(item.subject),
            by_user=any(authors[seq] == "user" for seq in seqs),
            by_character=any(authors[seq] == "character" for seq in seqs),
        )
        items.append(
            {
                "dropped": False,
                "kind": item.kind,
                "provenance": provenance,
                "text": fold(f"{item.content} {item.value or ''}"),
            }
        )
    return items


def matches(groups: tuple[tuple[str, ...], ...], text: str) -> bool:
    """Tells whether every keyword group has a fragment inside the folded `text`."""
    return all(any(part in text for part in group) for group in groups)


def judge_window(window: Window, outputs: list[str]) -> dict[str, Any]:
    """Returns the evaluation of one window from its outputs in order (the first generation, then the repair).

    Only the first MAX_ATTEMPTS outputs count, and the first accepted one is the window's result, as in the job.
    `found` lists, per expected item, whether a stored item has one of its kinds, its provenance and every keyword
    group. `inferences_stated` lists, per inference, whether a stored item that matches it is `stated`.
    `canonical_changes` counts the stored items that match an expected `identity_candidate` but carry another kind:
    such an item would reach prompts as a fact and override the card (docs/memory-engine.md, guard 5)."""
    attempts = [check_output(text) for text in outputs[:MAX_ATTEMPTS]]
    accepted = next((index for index, (verdict, _) in enumerate(attempts) if verdict["accepted"]), None)
    record: dict[str, Any] = {
        "id": window.id,
        "attempts": [verdict for verdict, _ in attempts],
        "valid": accepted is not None,
        "accepted_attempt": None if accepted is None else accepted + 1,
        "dropped": 0,
        "stored": 0,
        "found": [False] * len(window.expected),
        "inferences_stated": [False] * len(window.inferences),
        "canonical_changes": 0,
    }
    if accepted is None:
        return record
    result = attempts[accepted][1]
    assert result is not None
    items = stored_items(window, result)
    kept = [item for item in items if not item["dropped"]]
    record["dropped"] = len(items) - len(kept)
    record["stored"] = len(kept)
    for index, expected in enumerate(window.expected):
        record["found"][index] = any(
            item["kind"] in expected.kinds
            and item["provenance"] == expected.provenance
            and matches(expected.groups, item["text"])
            for item in kept
        )
        if IDENTITY_CANDIDATE in expected.kinds:
            record["canonical_changes"] += sum(
                1 for item in kept if item["kind"] != IDENTITY_CANDIDATE and matches(expected.groups, item["text"])
            )
    for index, groups in enumerate(window.inferences):
        record["inferences_stated"][index] = any(
            item["provenance"] == "stated" and matches(groups, item["text"]) for item in kept
        )
    return record


def _target(name: str, value: float) -> dict[str, Any]:
    comparison, limit = TARGETS[name]
    passed = value >= limit if comparison == ">=" else value <= limit
    return {"value": round(value, 4), "target": f"{comparison} {limit}", "passed": passed}


def score_document(request: dict[str, Any], windows_text: str | None = None) -> dict[str, Any]:
    """Returns the evaluation of the windows `ids` (all when null) from `outputs`, a list of texts per window id (a
    window without outputs counts as invalid): every window's record, the totals, each target of TARGETS with its value
    and verdict, and `passed` when all four are met. A rate over no item is 1.0 for the found and valid rates and 0.0
    for the inference rate, so an empty part never fails a target by itself. Raises KeyError for an unknown id."""
    _, windows = load_windows(windows_text if windows_text is not None else WINDOWS_PATH.read_text("utf-8"))
    chosen = _selected(windows, request.get("ids"))
    outputs = request.get("outputs") or {}
    records = [judge_window(window, [str(text) for text in outputs.get(window.id) or []]) for window in chosen]
    expected = sum(len(record["found"]) for record in records)
    found = sum(sum(record["found"]) for record in records)
    inferences = sum(len(record["inferences_stated"]) for record in records)
    stated = sum(sum(record["inferences_stated"]) for record in records)
    valid = sum(1 for record in records if record["valid"])
    changes = sum(record["canonical_changes"] for record in records)
    totals = {
        "windows": len(records),
        "valid_windows": valid,
        "repaired_windows": sum(1 for record in records if record["accepted_attempt"] == 2),
        "expected_items": expected,
        "found_items": found,
        "inferences": inferences,
        "inferences_stated": stated,
        "canonical_changes": changes,
        "stored_items": sum(record["stored"] for record in records),
        "dropped_items": sum(record["dropped"] for record in records),
    }
    targets = {
        "found_rate": _target("found_rate", found / expected if expected else 1.0),
        "inferences_stated_rate": _target("inferences_stated_rate", stated / inferences if inferences else 0.0),
        "canonical_changes": _target("canonical_changes", float(changes)),
        "valid_rate": _target("valid_rate", valid / len(records) if records else 1.0),
    }
    return {
        "windows": records,
        "totals": totals,
        "targets": targets,
        "passed": all(target["passed"] for target in targets.values()),
    }


def main(arguments: list[str]) -> int:
    """Runs the action named by `arguments` on the JSON object of standard input and prints its document. Returns 2,
    printing the usage, for a wrong action, and 0 otherwise; a malformed request raises, so the process fails."""
    if len(arguments) != 1 or arguments[0] not in ("prompt", "check", "score"):
        print(USAGE, file=sys.stderr)
        return 2
    request = json.loads(sys.stdin.read())
    if arguments[0] == "prompt":
        document = prompt_document(request)
    elif arguments[0] == "check":
        document = {key: check_output(str(text))[0] for key, text in (request.get("outputs") or {}).items()}
    else:
        document = score_document(request)
    print(json.dumps(document, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
