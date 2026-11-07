"""ContextBuilder: composes the prompt of one reply within the runtime's real window (docs/memory-engine.md, Context
composition; docs/prompting.md, System blocks and Length bands; ADR 0031).

The blocks fill the four parts of lyrveil.prompts.formatter.ChatParts: the stable text (blocks 1, 2, 3, the
conversation's scenario copy and block 6), the history (block 7), the dynamic context (blocks 4, 5 and 9) and the
current message (block 8). Every count that decides is the runtime's (TokenCounter): blocks and messages are counted one
by one to choose what to try, and the whole prompt is counted with `special` before it is sent. The retriever's
estimate only packs block 5. Nothing is written: the caller records the memory use after the reply is committed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from anyio import to_thread

from lyrveil.context.blocks import (
    OPTIONAL_IDENTITY,
    anchor,
    canonical_identity,
    communication_profile,
    global_instructions,
    memory_block,
    reply_length,
    stable_text,
    state_block,
    summary_block,
)
from lyrveil.context.minimal import UserProfile, scenario_block
from lyrveil.context.tokens import TokenCounter
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.messages import Author, Message
from lyrveil.memory.retrieval import is_past_intent
from lyrveil.memory.summaries import ACTIVE, Checkpoint, choose_summaries, coverage_gaps
from lyrveil.ports.memory_retriever import MemoryRetriever, RetrievalQuery, RetrievalResult, RetrievedMemory
from lyrveil.ports.text_runtime import TextRuntime
from lyrveil.prompts.formatter import ChatParts, FormattedPrompt, PromptFormatter, Turn

# docs/memory-engine.md (Context composition): the maxima of blocks 3, 5 and 6, the floor of block 7 and the cap of
# block 8, in tokens.
COMMUNICATION_MAX_TOKENS = 500
MEMORY_MAX_TOKENS = 1400
SUMMARY_MAX_TOKENS = 900
HISTORY_FLOOR_TOKENS = 800
CURRENT_MAX_TOKENS = 2048
# "while total > available (at most 3 rounds)": the most whole-prompt counts after the first.
MAX_ROUNDS = 3
# The retrieval query is the current message and the turns just before it (docs/memory-engine.md, Retrieval contract).
QUERY_TURNS = 2

# The reductions as Assembly.reductions names them, in the documented order. The last is not in the documented list: it
# applies only when everything before it is spent, because a prompt longer than the window would lose its start, the
# global instructions, inside the runtime.
EXAMPLES = "examples"
SUMMARIES = "summaries"
MEMORIES = "memories"
HISTORY = "history"
IDENTITY = "identity"
STATE = "state"
HISTORY_BELOW_FLOOR = "history_below_floor"
REDUCTION_ORDER = (EXAMPLES, SUMMARIES, MEMORIES, HISTORY, IDENTITY, STATE, HISTORY_BELOW_FLOOR)

# The keys of Assembly.blocks: the token count of each block of the final prompt.
BLOCK_NAMES = (
    "global",
    "identity",
    "communication",
    "scenario",
    "summary",
    "history",
    "state",
    "memories",
    "anchor",
    "current",
)


@dataclass(frozen=True)
class ContextSettings:
    """The `context.*` settings of docs/memory-engine.md (Settings) the backend has before the settings store exists.

    The reply reserve is not one of them: it is the band's `max_length` capped by `llm.max_length`
    (lyrveil.context.blocks.reply_length). Raises ValueError outside the documented ranges.
    """

    safety_margin_tokens: int = 64
    memory_target_tokens: int = 900
    summary_target_tokens: int = 600

    def __post_init__(self) -> None:
        if not 32 <= self.safety_margin_tokens <= 256:
            raise ValueError("safety_margin_tokens must be between 32 and 256")
        if not 0 <= self.memory_target_tokens <= MEMORY_MAX_TOKENS:
            raise ValueError(f"memory_target_tokens must be between 0 and {MEMORY_MAX_TOKENS}")
        if not 0 <= self.summary_target_tokens <= SUMMARY_MAX_TOKENS:
            raise ValueError(f"summary_target_tokens must be between 0 and {SUMMARY_MAX_TOKENS}")


@dataclass(frozen=True)
class StateText:
    """Block 4, already rendered as lines (lyrveil.context.blocks.state_lines). `lines` are always kept; `details` are
    the last thing the budget removes. Empty for a chat whose state was never written; the time context is P07.4's."""

    lines: tuple[str, ...] = ()
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class SummaryText:
    """An active checkpoint of the conversation with its prose."""

    checkpoint: Checkpoint
    text: str


@dataclass(frozen=True)
class ContextRequest:
    """Everything one reply prompt reads, loaded by the caller.

    `history` holds the readable messages before `current`, oldest first, with no system message and no empty text;
    `current` is the user message the reply answers. `summaries` are the conversation's active checkpoints. `now` must
    be aware. `notes` are the corrective notes of the anchor (at most two are used); `previous_memory_ids` the memory
    selection of the previous reply, which keeps block 5 stable. `extra_line`, when not empty, is one more line of the
    dynamic context after the anchor, never reduced: the image caption's request (image_caption_line).
    """

    character_id: str
    conversation_id: str
    card: CharacterCard
    user: UserProfile
    language: str
    shares_memory: bool
    history: Sequence[Message]
    current: Message
    summaries: Sequence[SummaryText]
    now: datetime
    scenario: Mapping[str, object] | None = None
    state: StateText = field(default_factory=StateText)
    topic: str | None = None
    notes: Sequence[str] = ()
    previous_memory_ids: frozenset[str] = frozenset()
    extra_line: str = ""


@dataclass(frozen=True)
class Assembly:
    """A built prompt and what it holds, for the request and the prompt-assembly record.

    `max_length` is the reply's limit and reserve; `available` is `context_limit - max_length - safety margin`, and
    `token_total` the runtime's count of `prompt.text` with `special`. `blocks` holds the runtime's count of each block
    of BLOCK_NAMES (`history` is the sum of the turns with their markers). `memories` are the items in block 5, best
    first; `summary_ids` the checkpoints in block 6, oldest first; `gaps` the seq ranges between them and the history
    that no checkpoint covers. `history_seqs` are the messages of block 7. `reductions` names the reductions applied, in
    REDUCTION_ORDER. `current_truncated` tells that the current message was cut to CURRENT_MAX_TOKENS in the prompt.
    """

    prompt: FormattedPrompt
    max_length: int
    context_limit: int
    available: int
    token_total: int
    blocks: Mapping[str, int]
    example_count: int
    memories: tuple[RetrievedMemory, ...]
    summary_ids: tuple[str, ...]
    gaps: tuple[tuple[int, int], ...]
    history_seqs: tuple[int, ...]
    reductions: tuple[str, ...]
    current_truncated: bool

    @property
    def fits(self) -> bool:
        """False only when even the parts that are never removed exceed the window; the prompt is then sent as is."""
        return self.token_total <= self.available


def split_messages(messages: Sequence[Message]) -> tuple[list[Message], Message] | None:
    """Returns the history and the user message a reply answers, from the chat's messages before the reply, oldest
    first.

    The newest user message is the current one and the messages before it are the history; character messages after
    it, which only a deleted user message can leave, are dropped. System messages and empty texts are never part of
    either. Returns None when no user message has text.
    """
    readable = [message for message in messages if message.author is not Author.SYSTEM and message.content]
    for index in range(len(readable) - 1, -1, -1):
        if readable[index].author is Author.USER:
            return readable[:index], readable[index]
    return None


def _turn(message: Message) -> Turn:
    return Turn("user" if message.author is Author.USER else "character", message.content)


@dataclass
class _Draft:
    """What one build has chosen so far. The reductions change it in place."""

    current: str
    examples: int
    summaries: list[SummaryText]
    memories: list[RetrievedMemory]
    history: list[Message] = field(default_factory=list)
    dropped: set[str] = field(default_factory=set)
    state_details: bool = True


class ContextBuilder:
    """Builds reply prompts with the counts of `runtime` and the memories of `retriever`. The TokenCounter it holds
    keeps counts between replies. Use it only under the reply's llm lease."""

    def __init__(
        self,
        runtime: TextRuntime,
        retriever: MemoryRetriever,
        settings: ContextSettings | None = None,
        *,
        counter: TokenCounter | None = None,
    ) -> None:
        self._runtime = runtime
        self._retriever = retriever
        self._settings = settings or ContextSettings()
        self._counter = counter or TokenCounter(runtime)

    async def build(self, request: ContextRequest, formatter: PromptFormatter, llm_max_length: int) -> Assembly:
        """Returns the prompt of `request` in `formatter`'s family, and what it holds.

        The window is the context size the runtime reports. The reply reserve is the band's `max_length` capped by
        `llm_max_length`, and the safety margin is kept free too. Blocks 1, 2, 3 (examples in saved order while block 3
        stays within COMMUNICATION_MAX_TOKENS), 4, 8 (cut to CURRENT_MAX_TOKENS) and 9 are rendered first, memories and
        summaries are chosen at their targets, and the history takes what is left, from the newest message back,
        never less than HISTORY_FLOOR_TOKENS. When the whole history fits, the room left goes to memories, then
        summaries, up to their maxima. The whole prompt is then counted; while it is over `available` it is reduced in
        REDUCTION_ORDER and counted again, at most MAX_ROUNDS times. Raises what the runtime or the retriever raise.
        """
        info = await self._runtime.info()
        run = _Run(self._counter, self._retriever, self._settings, request, formatter, info.model)
        return await run.assemble(info.max_context_length, llm_max_length)


class _Run:
    """One build: the request, the formatter's family and the tokenizer its counts belong to."""

    def __init__(
        self,
        counter: TokenCounter,
        retriever: MemoryRetriever,
        settings: ContextSettings,
        request: ContextRequest,
        formatter: PromptFormatter,
        tokenizer_id: str,
    ) -> None:
        self._counter = counter
        self._retriever = retriever
        self._settings = settings
        self._request = request
        self._formatter = formatter
        self._tokenizer = tokenizer_id
        self._turn_costs: dict[str, int] = {}
        self._overhead = 0
        self._global = global_instructions(request.card, request.user, request.language)
        self._scenario = scenario_block(request.scenario)
        # The extra line closes the dynamic context, so it is counted with the anchor, which is never reduced either.
        extra = " ".join(request.extra_line.split())
        self._anchor = "\n".join(part for part in (anchor(request.card, request.user, request.notes), extra) if part)

    async def assemble(self, limit: int, llm_max_length: int) -> Assembly:
        request, settings = self._request, self._settings
        max_length = reply_length(request.card.communication.message_length, llm_max_length)
        available = limit - max_length - settings.safety_margin_tokens
        self._overhead = await self._turn_overhead()
        current, truncated = await self._current_text()
        retrieved = await self._retrieve(settings.memory_target_tokens)
        by_id = {summary.checkpoint.id: summary for summary in request.summaries}
        checkpoints = [summary.checkpoint for summary in request.summaries]
        # Everything up to the newest active checkpoint is summarized; the history starts after it.
        boundary = max((c.to_seq for c in checkpoints if c.status == ACTIVE), default=0)
        candidates = [message for message in request.history if message.seq > boundary]
        summary_budget = settings.summary_target_tokens
        selection = choose_summaries(checkpoints, boundary + 1, summary_budget)
        draft = _Draft(
            current=current,
            examples=await self._examples_that_fit(),
            summaries=[by_id[c.id] for c in selection.checkpoints],
            memories=list(retrieved.items),
        )
        _, base = await self._count(draft)
        room = available - base
        draft.history, used = await self._fill(candidates, max(room, HISTORY_FLOOR_TOKENS))
        everything = len(draft.history) == len(candidates)
        if everything:
            spare = room - used
            if spare > 0 and 0 < settings.memory_target_tokens < MEMORY_MAX_TOKENS:
                wider = await self._retrieve(min(MEMORY_MAX_TOKENS, settings.memory_target_tokens + spare))
                spare -= max(0, wider.token_cost - retrieved.token_cost)
                draft.memories = list(wider.items)
            if spare > 0 and 0 < summary_budget < SUMMARY_MAX_TOKENS:
                summary_budget = min(SUMMARY_MAX_TOKENS, summary_budget + spare)
        # Every checkpoint ends at or before `boundary`, so any later start of the history chooses the same ones.
        recent_from = draft.history[0].seq if draft.history else request.current.seq
        selection = choose_summaries(checkpoints, recent_from, summary_budget)
        draft.summaries = [by_id[c.id] for c in selection.checkpoints]

        prompt, total = await self._count(draft)
        rounds = 0
        if total > available and not everything and room >= HISTORY_FLOOR_TOKENS:
            # The history is the remainder: what the turn counts missed comes out of it, before any block is reduced,
            # so a small miss never changes the stable text.
            rounds += 1
            await self._reduce(draft, total - available, [], (HISTORY,))
            prompt, total = await self._count(draft)
        applied: list[str] = []
        while total > available and rounds < MAX_ROUNDS:
            rounds += 1
            await self._reduce(draft, total - available, applied, REDUCTION_ORDER)
            prompt, total = await self._count(draft)

        recent_from = draft.history[0].seq if draft.history else request.current.seq
        return Assembly(
            prompt=prompt,
            max_length=max_length,
            context_limit=limit,
            available=available,
            token_total=total,
            blocks=await self._block_counts(draft),
            example_count=draft.examples,
            memories=tuple(draft.memories),
            summary_ids=tuple(summary.checkpoint.id for summary in draft.summaries),
            gaps=coverage_gaps([summary.checkpoint for summary in draft.summaries], recent_from),
            history_seqs=tuple(message.seq for message in draft.history),
            reductions=tuple(applied),
            current_truncated=truncated,
        )

    # Rendering

    def _identity(self, draft: _Draft) -> str:
        return canonical_identity(self._request.card, frozenset(draft.dropped))

    def _communication(self, draft: _Draft) -> str:
        return communication_profile(self._request.card, self._request.user, draft.examples)

    def _summary(self, draft: _Draft) -> str:
        return summary_block([summary.text for summary in draft.summaries])

    def _state(self, draft: _Draft) -> str:
        state = self._request.state
        return state_block(state.lines, state.details if draft.state_details else ())

    def _parts(self, draft: _Draft) -> ChatParts:
        request = self._request
        return ChatParts(
            stable_text=stable_text(
                self._global, self._identity(draft), self._communication(draft), self._scenario, self._summary(draft)
            ),
            history=tuple(_turn(message) for message in draft.history),
            state=self._state(draft),
            memories=memory_block(draft.memories),
            anchor=self._anchor,
            character_first_name=request.card.identity.first_name,
            user_display_name=request.user.display_name,
        )

    # Counting

    async def _tokens(self, text: str) -> int:
        return await self._counter.count(text, self._tokenizer)

    async def _count(self, draft: _Draft) -> tuple[FormattedPrompt, int]:
        """Renders the draft and returns it with the runtime's count of the whole prompt."""
        prompt = self._formatter.chat(self._parts(draft), draft.current)
        return prompt, await self._counter.count(prompt.text, self._tokenizer, special=True)

    async def _turn_overhead(self) -> int:
        """Returns what the markers of one history turn add to its text in this family, plus one token for the joins.

        Measured on a fixed probe, two prompts that differ by one user and one character turn, so the counter's cache
        keeps it for the model. It only prices turns for the fill; the whole prompt is still counted.
        """
        probe = ChatParts(
            stable_text="Sistema.",
            history=(),
            state="",
            memories="",
            anchor="",
            character_first_name="Alba",
            user_display_name="Utente",
        )
        turns = (Turn("user", "Uno."), Turn("character", "Due."))
        bare = self._formatter.chat(probe, "Ciao.").text
        full = self._formatter.chat(replace(probe, history=turns), "Ciao.").text
        added = await self._counter.count(full, self._tokenizer, special=True)
        added -= await self._counter.count(bare, self._tokenizer, special=True)
        for turn in turns:
            added -= await self._tokens(turn.text)
        return max(0, -(-added // len(turns))) + 1

    async def _turn_cost(self, message: Message) -> int:
        cost = self._turn_costs.get(message.id)
        if cost is None:
            cost = await self._tokens(message.content) + self._overhead
            self._turn_costs[message.id] = cost
        return cost

    async def _block_counts(self, draft: _Draft) -> dict[str, int]:
        texts = {
            "global": self._global,
            "identity": self._identity(draft),
            "communication": self._communication(draft),
            "scenario": self._scenario,
            "summary": self._summary(draft),
            "state": self._state(draft),
            "memories": memory_block(draft.memories),
            "anchor": self._anchor,
            "current": draft.current,
        }
        counts = {name: await self._tokens(text) for name, text in texts.items()}
        counts["history"] = sum([await self._turn_cost(message) for message in draft.history])
        return {name: counts[name] for name in BLOCK_NAMES}

    # Choosing

    async def _current_text(self) -> tuple[str, bool]:
        """Returns the current message for the prompt and whether it was cut: a text over CURRENT_MAX_TOKENS keeps its
        start, cut a little under the proportional length and counted again until the count fits."""
        text = self._request.current.content
        tokens = await self._tokens(text)
        truncated = False
        while tokens > CURRENT_MAX_TOKENS and text:
            keep = min(len(text) - 1, len(text) * CURRENT_MAX_TOKENS * 95 // (tokens * 100))
            text = text[: max(0, keep)]
            tokens = await self._tokens(text)
            truncated = True
        return text, truncated

    async def _examples_that_fit(self) -> int:
        """Returns how many examples block 3 shows: in saved order, stopping before the first one that would take the
        block over COMMUNICATION_MAX_TOKENS."""
        request = self._request
        examples = request.card.communication.examples
        for shown in range(1, len(examples) + 1):
            if await self._tokens(communication_profile(request.card, request.user, shown)) > COMMUNICATION_MAX_TOKENS:
                return shown - 1
        return len(examples)

    async def _retrieve(self, budget: int) -> RetrievalResult:
        if budget <= 0:
            return RetrievalResult(items=(), token_cost=0, candidate_count=0)
        request = self._request
        recent = [message.content for message in request.history[-QUERY_TURNS:]]
        query = RetrievalQuery(
            character_id=request.character_id,
            conversation_id=request.conversation_id,
            text="\n".join([*recent, request.current.content]),
            topic=request.topic,
            language=request.language,
            token_budget=budget,
            now=request.now,
            include_shared=request.shares_memory,
            past_intent=is_past_intent(request.current.content, request.language),
            previous_ids=request.previous_memory_ids,
        )
        # The retriever reads the database: off the event loop, like every other read of a reply.
        return await to_thread.run_sync(self._retriever.retrieve_relevant_memories, query)

    async def _fill(self, candidates: Sequence[Message], budget: int) -> tuple[list[Message], int]:
        """Returns the newest candidates whose turn costs fit `budget`, oldest first, and their total cost. Stops at the
        first one that does not fit, so the history stays contiguous."""
        kept: list[Message] = []
        used = 0
        for message in reversed(candidates):
            cost = await self._turn_cost(message)
            if used + cost > budget:
                break
            kept.append(message)
            used += cost
        kept.reverse()
        return kept, used

    # Reducing

    async def _reduce(self, draft: _Draft, overshoot: int, applied: list[str], names: Sequence[str]) -> None:
        """Applies the reductions `names`, in order and each until it is spent, until the runtime counts of what they
        removed reach `overshoot`. Adds the name of each reduction that removed something to `applied`."""
        saved = 0
        for name in names:
            while saved < overshoot:
                gained = await self._step(name, draft)
                if gained is None:
                    break
                saved += gained
                if name not in applied:
                    applied.append(name)
            if saved >= overshoot:
                return

    async def _step(self, name: str, draft: _Draft) -> int | None:
        """Removes the next piece of reduction `name` and returns the tokens it saved, or None when nothing is left."""
        if name == EXAMPLES:
            if draft.examples == 0:
                return None
            before = await self._tokens(self._communication(draft))
            draft.examples -= 1
            return before - await self._tokens(self._communication(draft))
        if name == SUMMARIES:
            if not draft.summaries:
                return None
            before = await self._tokens(self._summary(draft))
            del draft.summaries[0]
            return before - await self._tokens(self._summary(draft))
        if name == MEMORIES:
            if not draft.memories:
                return None
            before = await self._tokens(memory_block(draft.memories))
            # Items are best first and a conflict group is adjacent: the lowest unit goes whole.
            group = draft.memories.pop().conflict_group_id
            while group is not None and draft.memories and draft.memories[-1].conflict_group_id == group:
                draft.memories.pop()
            return before - await self._tokens(memory_block(draft.memories))
        if name in (HISTORY, HISTORY_BELOW_FLOOR):
            if not draft.history:
                return None
            costs = [await self._turn_cost(message) for message in draft.history]
            floor = HISTORY_FLOOR_TOKENS if name == HISTORY else 0
            if sum(costs) - costs[0] < floor:
                return None
            del draft.history[0]
            return costs[0]
        if name == IDENTITY:
            full = self._identity(draft)
            for key in OPTIONAL_IDENTITY:
                if key in draft.dropped:
                    continue
                draft.dropped.add(key)
                reduced = self._identity(draft)
                if reduced != full:
                    return await self._tokens(full) - await self._tokens(reduced)
            return None
        if name == STATE:
            if not draft.state_details or not self._request.state.details:
                return None
            before = await self._tokens(self._state(draft))
            draft.state_details = False
            return before - await self._tokens(self._state(draft))
        raise ValueError(f"unknown reduction {name!r}")
