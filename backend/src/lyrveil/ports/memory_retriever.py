"""MemoryRetriever: the port through which the context builder asks for the memories and episodes of one reply
(docs/memory-engine.md, Retrieval contract; ADR 0011)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

DEFAULT_MAX_ITEMS = 24

ItemType = Literal["memory", "episode"]


@dataclass(frozen=True)
class RetrievalQuery:
    """What one reply needs to remember.

    `text` is the latest user message and the two turns before it. `include_shared` is the conversation's
    `shares_memory`; `past_intent` tells that the user asks about the past (lyrveil.memory.retrieval.is_past_intent),
    which lets superseded memories in. `previous_ids` is the selection of the previous reply. `now` must be aware.
    """

    character_id: str
    conversation_id: str
    text: str
    topic: str | None
    language: str
    token_budget: int
    now: datetime
    include_shared: bool
    past_intent: bool
    previous_ids: frozenset[str]
    max_items: int = DEFAULT_MAX_ITEMS


@dataclass(frozen=True)
class ScoreParts:
    """The parts of an item's score, each in 0 to 1 before weighting, kept for diagnostics (Memory Studio)."""

    fts: float
    importance: float
    recency: float
    topic: float
    kind: float
    confidence: float
    status_factor: float
    locked_bonus: float
    previous_bonus: float


@dataclass(frozen=True)
class RetrievedMemory:
    """One selected memory or episode, ready for the prompt.

    `kind` is the memory kind, or `episode`. `status` is `active`, `uncertain` or `superseded` for a memory and
    `active` for an episode. `label` is the prompt label (`[sicuro]`, `[incerto]`, `[storia]`, `[per ora]`,
    `[passato]`, `[episodio]`) and `line` the rendered prompt line, whose estimated cost is `token_cost`.
    """

    id: str
    item_type: ItemType
    kind: str
    content: str
    status: str
    label: str
    line: str
    score: float
    parts: ScoreParts
    token_cost: int
    subject_key: str | None
    conflict_group_id: str | None


@dataclass(frozen=True)
class RetrievalResult:
    """The selection, best score first (members of one conflict group are adjacent), its total estimated token cost
    and how many candidates were scored."""

    items: tuple[RetrievedMemory, ...]
    token_cost: int
    candidate_count: int

    @property
    def ids(self) -> frozenset[str]:
        """The identifiers of the selection, to pass as `previous_ids` on the next reply."""
        return frozenset(item.id for item in self.items)


class MemoryRetriever(Protocol):
    """Finds the memories a reply should see. A semantic or hybrid retriever implements the same Protocol."""

    def retrieve_relevant_memories(self, query: RetrievalQuery) -> RetrievalResult:
        """Return the best memories and episodes that fit `token_budget`.

        Results always belong to `character_id`, and to `conversation_id` or, when
        `include_shared` is true, to the shared scope. Each item holds its score parts
        for diagnostics. An empty result is valid. Raises only on a database failure.
        """
        ...
