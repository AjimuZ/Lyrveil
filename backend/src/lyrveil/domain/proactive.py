"""The output schema of the `decide` stage of a proactive message (docs/scheduler.md, Generation flow), from which both
the grammar and the validation derive (ADR 0013)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

# Changing the model below must change `prompts.proactive.TEMPLATE_VERSION` too.
MAX_REASON_CHARS = 200


class ProactiveDecision(BaseModel):
    """Whether the character writes first, and why. `reason` is short, in Italian, and when `send` is true it is what
    the generation stage is told ("Motivo"); when false it is the reason the event is skipped for. `hook_ref` names the
    memory or open thread that motivates the message, as the model wrote it, or None."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    send: bool
    reason: Annotated[str, StringConstraints(max_length=MAX_REASON_CHARS)]
    hook_ref: str | None = None
