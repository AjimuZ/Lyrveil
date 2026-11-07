"""Progressive summaries without a database (docs/memory-engine.md, Progressive summaries): which messages the next
level-0 checkpoint covers, which checkpoints a rollup merges, which a prompt takes, and the hashes that tell a covered
range changed. Pure: the callers read and write the database."""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from lyrveil.memory.retrieval import estimate_tokens
from lyrveil.memory.window import WindowMessage, readable_before_pending, window_hash

# docs/memory-engine.md (Progressive summaries), the rules table and `summary.*` settings.
DEFAULT_CHUNK_MESSAGES = 40
MIN_CHUNK_MESSAGES = 20
MAX_CHUNK_MESSAGES = 100
DEFAULT_RECENT_TAIL = 30
MIN_RECENT_TAIL = 10
MAX_RECENT_TAIL = 100
# A chunk is also due once its messages reach this many tokens, whatever their number.
CHUNK_TOKENS = 2400
# Six consecutive checkpoints of a level merge their oldest four into one of the next level.
ROLLUP_TRIGGER = 6
ROLLUP_TAKE = 4
# A prompt takes an older rollup only when it costs at most this many tokens (docs/memory-engine.md, Choosing
# summaries for a prompt).
ROLLUP_PROMPT_TOKENS = 200

ACTIVE = "active"
SUPERSEDED = "superseded"
INVALIDATED = "invalidated"


@dataclass(frozen=True)
class SummarySettings:
    """The summary settings the backend has before the settings store exists: `chunk_messages` is
    `summary.chunk_messages` and `recent_tail_messages` is `summary.recent_tail_messages`. Raises ValueError outside
    their allowed ranges."""

    chunk_messages: int = DEFAULT_CHUNK_MESSAGES
    recent_tail_messages: int = DEFAULT_RECENT_TAIL

    def __post_init__(self) -> None:
        if not MIN_CHUNK_MESSAGES <= self.chunk_messages <= MAX_CHUNK_MESSAGES:
            raise ValueError(f"chunk_messages must be between {MIN_CHUNK_MESSAGES} and {MAX_CHUNK_MESSAGES}")
        if not MIN_RECENT_TAIL <= self.recent_tail_messages <= MAX_RECENT_TAIL:
            raise ValueError(f"recent_tail_messages must be between {MIN_RECENT_TAIL} and {MAX_RECENT_TAIL}")


@dataclass(frozen=True)
class Checkpoint:
    """A stored checkpoint without its content. `level` is 0 for a message range and 1 and above for a rollup;
    `status` is ACTIVE, SUPERSEDED or INVALIDATED. Always holds: `from_seq <= to_seq`."""

    id: str
    level: int
    from_seq: int
    to_seq: int
    version: int
    status: str
    token_count: int
    source_hash: str


@dataclass(frozen=True)
class Chunk:
    """The messages one level-0 checkpoint covers: the readable ones from `from_seq` (the cursor plus one) to `to_seq`,
    oldest first, and their hash."""

    from_seq: int
    to_seq: int
    messages: tuple[WindowMessage, ...]
    hash: str


@dataclass(frozen=True)
class Rollup:
    """The checkpoints that become one of `level`, oldest first; they are contiguous and all of `level - 1`."""

    level: int
    sources: tuple[Checkpoint, ...]

    @property
    def from_seq(self) -> int:
        return self.sources[0].from_seq

    @property
    def to_seq(self) -> int:
        return self.sources[-1].to_seq

    @property
    def hash(self) -> str:
        return children_hash(self.sources)


@dataclass(frozen=True)
class Selection:
    """The checkpoints a prompt takes, oldest first, and the seq ranges `(first, last)` between them and the recent
    messages that no chosen checkpoint covers (the gaps the prompt assembly logs)."""

    checkpoints: tuple[Checkpoint, ...]
    gaps: tuple[tuple[int, int], ...]


def children_hash(sources: Sequence[Checkpoint]) -> str:
    """Returns the SHA-256, lowercase hex, of the ids and hashes of the checkpoints a rollup merges, in order."""
    digest = hashlib.sha256()
    for source in sources:
        digest.update(f"{source.id}\x1f{source.source_hash}\x1e".encode())
    return digest.hexdigest()


def next_chunk(
    after_cursor: Sequence[WindowMessage],
    cursor: int,
    tail_start: int | None,
    settings: SummarySettings,
    cost: Callable[[str], int] = estimate_tokens,
) -> Chunk | None:
    """Returns the next level-0 range after `cursor`, or None when it is not due yet.

    `after_cursor` are the messages with a seq above `cursor`, ordered by seq. `tail_start` is the seq of the oldest of
    the conversation's newest `recent_tail_messages` readable messages (None when it has no more than that many): the
    tail is never summarized, and neither is anything from the first pending reply on. Of the messages before them, a
    chunk takes the first `chunk_messages`, or fewer when their cost reaches CHUNK_TOKENS first; with neither reached,
    nothing is due. Deleted, failed, empty and system messages are passed over but fall inside the range.
    """
    if tail_start is None:
        return None
    eligible = [message for message in readable_before_pending(after_cursor) if message.seq < tail_start]
    picked: list[WindowMessage] = []
    tokens = 0
    for message in eligible:
        picked.append(message)
        tokens += cost(message.content)
        if len(picked) >= settings.chunk_messages or tokens >= CHUNK_TOKENS:
            return Chunk(cursor + 1, picked[-1].seq, tuple(picked), window_hash(picked))
    return None


def _runs(checkpoints: Sequence[Checkpoint]) -> list[list[Checkpoint]]:
    """Splits checkpoints ordered by `from_seq` into runs where each starts right after the previous one ends."""
    runs: list[list[Checkpoint]] = []
    for checkpoint in checkpoints:
        if runs and runs[-1][-1].to_seq + 1 == checkpoint.from_seq:
            runs[-1].append(checkpoint)
        else:
            runs.append([checkpoint])
    return runs


def next_rollup(checkpoints: Sequence[Checkpoint]) -> Rollup | None:
    """Returns the rollup that is due, or None.

    Only ACTIVE checkpoints count. At each level from the lowest, the checkpoints not yet inside a rollup (those that
    start after the end of the newest active checkpoint of the next level) form runs of contiguous ranges; the first run
    of at least ROLLUP_TRIGGER gives its oldest ROLLUP_TAKE. Their rows stay as they are.
    """
    active = [checkpoint for checkpoint in checkpoints if checkpoint.status == ACTIVE]
    for level in sorted({checkpoint.level for checkpoint in active}):
        merged_to = max((c.to_seq for c in active if c.level == level + 1), default=0)
        free = sorted((c for c in active if c.level == level and c.from_seq > merged_to), key=lambda c: c.from_seq)
        for run in _runs(free):
            if len(run) >= ROLLUP_TRIGGER:
                return Rollup(level + 1, tuple(run[:ROLLUP_TAKE]))
    return None


def choose_summaries(checkpoints: Sequence[Checkpoint], recent_from_seq: int, budget: int) -> Selection:
    """Returns what a prompt takes within `budget` tokens, given that its recent messages start at `recent_from_seq`.

    Only ACTIVE checkpoints are used, never an invalidated or superseded one. First the newest level-0 checkpoint that
    ends before the recent messages, then earlier ones walking backward while each ends right before the one after it
    and still fits; the walk ends at a checkpoint that does not fit or that leaves a gap. Then, when budget is left, the
    highest-level rollup that ends before everything chosen, if it costs at most ROLLUP_PROMPT_TOKENS and fits. A gap
    in the coverage is allowed and reported in `gaps`.
    """
    active = [c for c in checkpoints if c.status == ACTIVE]
    chosen: list[Checkpoint] = []
    left = budget
    ranges = sorted((c for c in active if c.level == 0 and c.to_seq < recent_from_seq), key=lambda c: -c.to_seq)
    for checkpoint in ranges:
        contiguous = not chosen or checkpoint.to_seq + 1 == chosen[0].from_seq
        if not contiguous or checkpoint.token_count > left:
            break
        chosen.insert(0, checkpoint)
        left -= checkpoint.token_count
    before = chosen[0].from_seq if chosen else recent_from_seq
    allowance = min(left, ROLLUP_PROMPT_TOKENS)
    older = [c for c in active if c.level >= 1 and c.to_seq < before and c.token_count <= allowance]
    if older:
        chosen.insert(0, max(older, key=lambda c: (c.level, c.to_seq)))
    return Selection(tuple(chosen), coverage_gaps(chosen, recent_from_seq))


def coverage_gaps(chosen: Sequence[Checkpoint], recent_from_seq: int) -> tuple[tuple[int, int], ...]:
    """Returns the seq ranges `(first, last)` that no checkpoint of `chosen` (oldest first) covers between them and the
    recent messages starting at `recent_from_seq`; nothing when `chosen` is empty."""
    gaps: list[tuple[int, int]] = []
    for earlier, later in pairwise(chosen):
        if earlier.to_seq + 1 < later.from_seq:
            gaps.append((earlier.to_seq + 1, later.from_seq - 1))
    if chosen and chosen[-1].to_seq + 1 < recent_from_seq:
        gaps.append((chosen[-1].to_seq + 1, recent_from_seq - 1))
    return tuple(gaps)


def clean_source_seqs(seqs: Sequence[int], first: int, last: int) -> list[int]:
    """Returns the distinct seqs of `seqs` inside `first` to `last`, ascending: a number the model made up or took from
    outside the covered range is dropped."""
    return sorted({seq for seq in seqs if first <= seq <= last})
