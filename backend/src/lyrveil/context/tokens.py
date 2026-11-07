"""TokenCounter: the runtime's token counts, cached in memory by text (docs/memory-engine.md, Token counting).

`POST /api/extra/tokencount` is the authority: every count comes from TextRuntime.count_tokens, never from an estimate.
A count is remembered under the tokenizer it came from, so blocks and messages that did not change are not sent again,
and a model change counts everything again.
"""

import hashlib
from collections import OrderedDict

from lyrveil.ports.text_runtime import TextRuntime

# About forty replies of a long chat: blocks, messages and whole prompts.
DEFAULT_CAPACITY = 8192


class TokenCounter:
    """Counts with `runtime` and keeps at most `capacity` counts, dropping the least recently used. Must be used only
    under the caller's llm lease, like the runtime. Raises what `count_tokens` raises; a failed count is not cached."""

    def __init__(self, runtime: TextRuntime, capacity: int = DEFAULT_CAPACITY) -> None:
        self._runtime = runtime
        self._capacity = capacity
        self._cache: OrderedDict[tuple[str, bool, str], int] = OrderedDict()

    async def count(self, text: str, tokenizer_id: str, *, special: bool = False) -> int:
        """Returns the runtime's count of `text` for the tokenizer `tokenizer_id` (the loaded model's name). `special`
        adds the start token the runtime prepends, as for a whole prompt. An empty text without `special` is 0."""
        if not text and not special:
            return 0
        key = (tokenizer_id, special, hashlib.sha256(text.encode("utf-8")).hexdigest())
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        counted = await self._runtime.count_tokens(text, special=special)
        self._cache[key] = counted
        if len(self._cache) > self._capacity:
            self._cache.popitem(last=False)
        return counted
