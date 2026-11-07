"""The output schema of memory extraction (docs/memory-engine.md, Output schema), from which both the grammar and the
validation derive (ADR 0013), and the fixed values that turn its fields into stored ones."""

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Stored on every run with the prompt version: changing a model below must change it, so that a window analyzed under
# the old schema is analyzed again instead of being taken as done (memory_extraction_runs is unique on both).
SCHEMA_VERSION = "1"

MemoryKind = Literal[
    "user_fact",
    "user_preference",
    "character_fact",
    "narrative_event",
    "promise",
    "decision",
    "open_thread",
    "boundary",
    "identity_candidate",
]
Relation = Literal["new", "duplicate", "update", "contradiction", "temporary", "correction"]
Confidence = Literal["low", "medium", "high"]

# docs/memory-engine.md (The memory record): the stored value of each confidence word, and the cap of an inference.
CONFIDENCE_VALUES: Mapping[str, float] = MappingProxyType({"low": 0.4, "medium": 0.7, "high": 0.95})
INFERRED_MAX_CONFIDENCE = 0.6
# The relations that act on an existing memory and therefore need a `target_ref`.
TARGETED_RELATIONS = frozenset({"duplicate", "update", "contradiction", "correction"})


class ExtractedMemory(BaseModel):
    """One fact the model proposes. `target_ref` names an existing memory shown in the prompt (`E3`); `source_seqs` are
    the numbers of the window messages that prove it."""

    model_config = ConfigDict(extra="forbid")

    relation: Relation
    target_ref: str | None = None
    kind: MemoryKind
    content: Annotated[str, StringConstraints(min_length=3, max_length=280)]
    subject: str | None = None
    value: str | None = None
    provenance: Literal["stated", "inferred", "fictional"]
    durability: Literal["lasting", "temporary"]
    valid_until: date | None = None
    importance: Annotated[int, Field(ge=1, le=5)]
    confidence: Confidence
    source_seqs: Annotated[list[int], Field(min_length=1, max_length=8)]


class ExtractedEpisode(BaseModel):
    """One significant event. `linked_episode_refs` name recent episodes shown in the prompt (`P1`)."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(min_length=3, max_length=120)]
    description: Annotated[str, StringConstraints(min_length=3, max_length=600)]
    participants: list[str]
    importance: Annotated[int, Field(ge=1, le=5)]
    source_seqs: Annotated[list[int], Field(min_length=1, max_length=12)]
    linked_episode_refs: list[str] = []


class RelationshipEvent(BaseModel):
    """A change of one relationship dimension with its reason."""

    model_config = ConfigDict(extra="forbid")

    dimension: Literal["familiarity", "trust", "confidence", "tension"]
    direction: Literal["up", "down"]
    magnitude: Literal["small", "medium", "large"]
    reason: Annotated[str, StringConstraints(min_length=3, max_length=200)]
    source_seqs: Annotated[list[int], Field(min_length=1, max_length=6)]


class StateUpdate(BaseModel):
    """The conversation state the window suggests."""

    model_config = ConfigDict(extra="forbid")

    emotional_state: str | None = None
    current_topic: str | None = None
    open_threads_add: list[str] = []
    open_threads_close: list[str] = []
    goals: list[str] = []
    narrative_change: str | None = None


class ExtractionResult(BaseModel):
    """The whole answer of one extraction run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    memories: Annotated[list[ExtractedMemory], Field(max_length=12)]
    episodes: Annotated[list[ExtractedEpisode], Field(max_length=3)]
    relationship_events: Annotated[list[RelationshipEvent], Field(max_length=4)]
    state: StateUpdate


def stored_confidence(confidence: Confidence, provenance: str) -> float:
    """Returns the confidence to store for a proposed item: the value of its word, capped at INFERRED_MAX_CONFIDENCE
    for an inference, which the `memories` table also enforces."""
    value = CONFIDENCE_VALUES[confidence]
    return min(value, INFERRED_MAX_CONFIDENCE) if provenance == "inferred" else value
