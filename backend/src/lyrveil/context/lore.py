"""Lore in the reply prompt (ADR 0049, Selection and order): the books a reply reads, the active entries it may hold,
the order blocks 11 and 12 keep and show them in, and the choice within the block and book budgets.

Pure: no database, no runtime and no model. The matcher (lyrveil.services.lore_matcher.active_lore) decides which
entries are active; the builder (lyrveil.context.builder) counts every rendered line with the runtime and calls
`select`. The same items and counts always give the same choice, so block 11 stays the same bytes from one reply to
the next (ADR 0031).
"""

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from lyrveil.domain.lore import BookKind, LoreBook, LoreEntry


@dataclass(frozen=True)
class LoreSource:
    """One book a reply reads, with its enabled entries by `insertion_order` and id. The books of a reply are the
    character's own book first, then the worlds linked to the character or to the conversation, each once."""

    book: LoreBook
    entries: tuple[LoreEntry, ...]

    @property
    def own(self) -> bool:
        """True for the character's own book, False for a world."""
        return self.book.kind is BookKind.CHARACTER


@dataclass(frozen=True)
class LoreItem:
    """An entry that is active for one reply, with what the selection reads.

    `title` and `content` are the stored texts, `{{char}}` and `{{user}}` included; the builder renders them. `own` is
    true for an entry of the character's own book. `constant` places the entry in block 11, the stable text; otherwise
    it is in block 12, the dynamic context. `matched_key` is the key, as typed, that activated the entry, None for a
    constant entry. `book_budget` is the book's `token_budget`, None when only the block's budget applies. `book_rank`
    is the book's place in the reply's book order and breaks the last ties.
    """

    entry_id: str
    book_id: str
    title: str
    content: str
    own: bool
    constant: bool
    priority: int
    insertion_order: int
    matched_key: str | None = None
    book_budget: int | None = None
    book_rank: int = 0


def content_hash(content: str) -> str:
    """Returns the SHA-256 of an entry's stored content, as 64 lowercase hex digits. `lore_refs` records it instead of
    the text, so a reader can tell whether the entry changed since a reply without the assembly keeping the text."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _order_key(item: LoreItem) -> tuple[int, int, int, int, str]:
    return (-item.priority, 0 if item.own else 1, item.insertion_order, item.book_rank, item.entry_id)


def ordered(items: Iterable[LoreItem]) -> list[LoreItem]:
    """Returns `items` in the order ADR 0049 keeps and shows them: `priority` highest first; on a tie the own book
    before the worlds; then the lower `insertion_order`; then the book's rank and the entry id, so the order never
    depends on the order the items were read in."""
    return sorted(items, key=_order_key)


def select(
    items: Sequence[LoreItem], costs: Mapping[str, int], header_cost: int, target: int, maximum: int
) -> tuple[LoreItem, ...]:
    """Returns the items one block holds, in the order of `items`, which must already be `ordered`.

    `costs` maps each entry id to the tokens of its rendered line, and `header_cost` is the tokens of the block's
    heading. The walk goes from the first item to the last:

    - An item whose line, with the heading, costs more than `maximum` never enters any prompt: it is left out and the
      walk goes on.
    - An item of a book that has reached its `book_budget` is left out, with every later item of that book; the other
      books go on. The book budget counts the lines only.
    - The block holds items while the heading and the lines stay within `target`. The first item that would take it
      over ends the walk, so a lower item never stays while a higher one is left out for room. An item over the target
      still opens an empty block when it fits `maximum`, so every entry within its block's maximum can enter a prompt.
    """
    kept: list[LoreItem] = []
    used = header_cost
    book_used: dict[str, int] = {}
    full_books: set[str] = set()
    for item in items:
        cost = costs[item.entry_id]
        if header_cost + cost > maximum or item.book_id in full_books:
            continue
        spent = book_used.get(item.book_id, 0)
        if item.book_budget is not None and spent + cost > item.book_budget:
            full_books.add(item.book_id)
            continue
        if used + cost > target and kept:
            break
        kept.append(item)
        used += cost
        book_used[item.book_id] = spent + cost
    return tuple(kept)
