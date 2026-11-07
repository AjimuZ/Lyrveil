"""The output schema of a summary checkpoint (docs/memory-engine.md, Progressive summaries), from which both the grammar
and the validation derive (ADR 0013)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Changing a model below must change `prompts.summary.TEMPLATE_VERSION` too: the checkpoint stores that version.
SCHEMA_VERSION = "1"
MAX_TEXT_CHARS = 1600

# One entry of a list is a short sentence, and a list has at most eight, so the details cannot outgrow the reply budget.
Entry = Annotated[str, StringConstraints(min_length=3, max_length=200)]
Entries = Annotated[list[Entry], Field(max_length=8)]


class SummaryResult(BaseModel):
    """The whole answer of one summary run. `text` is the compact prose a prompt shows; the lists keep the details a
    rollup or a person may need. `source_seqs` are the numbers of the messages the summary leans on (for a rollup, of
    the messages the earlier checkpoints cited)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    text: Annotated[str, StringConstraints(min_length=3, max_length=MAX_TEXT_CHARS)]
    events: Entries
    decisions: Entries
    relationship_changes: Entries
    unresolved: Entries
    key_facts: Entries
    source_seqs: Annotated[list[int], Field(max_length=24)]
