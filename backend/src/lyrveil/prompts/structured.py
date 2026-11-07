"""generate_structured: one structured task under its grammar, validated, with at most one repair (ADR 0013).

docs/prompting.md (Structured tasks): generate through `/api/v1/generate` with the grammar, parse and validate, and on
failure try once more with the error appended; a second failure raises StructuredOutputInvalidError. The output is
untrusted text: it is only parsed as JSON and handed to the caller's validation, never executed or logged.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lyrveil.logs import get_logger
from lyrveil.ports.text_runtime import TextRequest, TextRuntime

# The job error code of a structured task that failed validation on every attempt (docs/api.md, Errors).
STRUCTURED_OUTPUT_INVALID = "STRUCTURED_OUTPUT_INVALID"
# The first generation and the single repair.
MAX_ATTEMPTS = 2
NOT_JSON = "La risposta del modello non è un JSON valido."
NOT_AN_OBJECT = "La risposta del modello non è un oggetto JSON."

logger = get_logger(__name__)


class OutputRejectedError(Exception):
    """The caller's validation refused a parsed output. `detail` is Italian, names fields and never holds values,
    because it reaches the repair prompt and, after the last attempt, the job's `error_detail`."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class StructuredOutputInvalidError(Exception):
    """Every attempt was refused. `detail` is the reason of the last one, safe to show as OutputRejectedError's."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Repair:
    """What a repair prompt adds: the refused output and why it was refused."""

    previous_output: str
    error: str


@dataclass(frozen=True)
class StructuredResult[T]:
    """The accepted value and the number of generations it took: 1, or 2 after a repair."""

    value: T
    attempts: int


def _parse(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except ValueError:
        raise OutputRejectedError(NOT_JSON) from None
    if not isinstance(parsed, dict):
        raise OutputRejectedError(NOT_AN_OBJECT)
    return parsed


async def generate_structured[T](
    runtime: TextRuntime,
    *,
    prompt: Callable[[Repair | None], str],
    grammar: str,
    validate: Callable[[dict[str, Any]], T],
    genkey: str,
    max_length: int,
    temperature: float,
) -> StructuredResult[T]:
    """Returns the first output that `validate` accepts, within MAX_ATTEMPTS generations.

    `prompt(None)` is the first prompt and `prompt(repair)` the repair prompt. `validate` receives the parsed JSON
    object and raises OutputRejectedError to refuse it. Requires the caller's llm lease. Raises
    StructuredOutputInvalidError when every attempt is refused, and lets a TextRuntimeError through unchanged, for the
    scheduler to retry.
    """
    info = await runtime.info()
    repair: Repair | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = TextRequest(
            prompt=prompt(repair),
            max_context_length=info.max_context_length,
            max_length=max_length,
            genkey=genkey,
            temperature=temperature,
            grammar=grammar,
        )
        text = await runtime.generate(request)
        try:
            value = validate(_parse(text))
        except OutputRejectedError as refused:
            logger.info("structured_output_refused", attempt_count=attempt)
            repair = Repair(previous_output=text, error=refused.detail)
            continue
        return StructuredResult(value=value, attempts=attempt)
    assert repair is not None
    raise StructuredOutputInvalidError(repair.error)
