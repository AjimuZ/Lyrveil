"""The backend side of T-GPU-05: the character draft's real prompt and grammar, and the job's judgement of outputs.

tests/gpu/StructuredOutput.Tests.ps1 (repository root) runs this file in a container of the backend's test stage, with
no network, and sends the outputs of the real text model through it. So the gpu level generates with exactly what the
`character_draft` job sends and judges each output with exactly the job's validation (ADR 0013):

    python tests/gpu_probe/structured_task.py prompt < {"family": ..., "description": ..., "repair": null}
    python tests/gpu_probe/structured_task.py check < {"output": ...}

Each action reads one JSON object from standard input and prints one JSON line, ASCII only, so Windows PowerShell 5.1
reads it byte for byte. The descriptions are test data; nothing here is logged.
"""

import hashlib
import json
import sys
from typing import Any

from lyrveil.catalog import TextTemplate
from lyrveil.domain.card_draft import CharacterDraft, proposed_fields
from lyrveil.prompts import character_draft
from lyrveil.prompts.structured import MAX_ATTEMPTS, NOT_AN_OBJECT, NOT_JSON, OutputRejectedError, Repair
from lyrveil.runtimes.koboldcpp import TOTAL_TIMEOUT_S
from lyrveil.services.character_creator import DRAFT_GRAMMAR, DraftBlockedError, check_draft

USAGE = "usage: structured_task.py prompt|check < request.json"


def prompt_document(request: dict[str, Any]) -> dict[str, Any]:
    """Returns the generation of a new character's draft for `request`: the prompt in the format of `family`, with the
    repair of a refused output when `repair` holds `previous_output` and `error`; the grammar and the other values the
    job sends; the top-level keys the grammar writes, in order (`draft_keys`); and the bounds the job applies
    (attempts, total timeout of a generation).

    Raises KeyError when `family` or `description` is missing or the family is not a catalog template family.
    """
    family: TextTemplate = request["family"]
    raw_repair = request.get("repair")
    repair = None if raw_repair is None else Repair(str(raw_repair["previous_output"]), str(raw_repair["error"]))
    return {
        "task": "character_draft",
        "template_version": character_draft.TEMPLATE_VERSION,
        "family": family,
        "prompt": character_draft.render_prompt(family, str(request["description"]), None, repair),
        "grammar": DRAFT_GRAMMAR,
        "grammar_sha256": hashlib.sha256(DRAFT_GRAMMAR.encode("utf-8")).hexdigest(),
        "grammar_rules": DRAFT_GRAMMAR.count("\n"),
        "draft_keys": list(CharacterDraft.model_fields),
        "max_length": character_draft.MAX_LENGTH,
        "temperature": character_draft.TEMPERATURE,
        "max_attempts": MAX_ATTEMPTS,
        "total_timeout_s": TOTAL_TIMEOUT_S,
    }


def check_document(output: str) -> dict[str, Any]:
    """Returns how the `character_draft` job of a new character judges `output`.

    `json` says whether it parses as a JSON object and `keys` lists its top-level keys in order. `accepted` is the
    job's verdict; `blocked` says the content guard refused it; `detail` is the Italian reason of a refusal, which names
    fields and never values. An accepted output also gives `age` and `proposed_fields`, the number of card fields the
    draft filled in.
    """
    document: dict[str, Any] = {"json": False, "keys": [], "accepted": False, "blocked": False, "detail": ""}
    try:
        parsed = json.loads(output)
    except ValueError:
        document["detail"] = NOT_JSON
        return document
    if not isinstance(parsed, dict):
        document["detail"] = NOT_AN_OBJECT
        return document
    document["json"] = True
    document["keys"] = list(parsed)
    try:
        card = check_draft(parsed, None)
    except OutputRejectedError as refused:
        document["blocked"] = isinstance(refused, DraftBlockedError)
        document["detail"] = refused.detail
        return document
    document["accepted"] = True
    document["age"] = card.identity.age
    document["proposed_fields"] = len(proposed_fields(card, None))
    return document


def main(arguments: list[str]) -> int:
    """Runs the action named by `arguments` on the JSON object of standard input and prints its document. Returns 2,
    printing the usage, for a wrong action, and 0 otherwise; a malformed request raises, so the process fails."""
    if len(arguments) != 1 or arguments[0] not in ("prompt", "check"):
        print(USAGE, file=sys.stderr)
        return 2
    request = json.loads(sys.stdin.read())
    document = prompt_document(request) if arguments[0] == "prompt" else check_document(str(request["output"]))
    print(json.dumps(document, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
