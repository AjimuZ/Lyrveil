# ADR 0043: A windowed message list of our own instead of the CDK virtual scroll

Status: accepted (2026-11-09). Supersedes: none. Refines the "virtual scrolling with `@angular/cdk`" line of
[Frontend](../frontend.md#2-conversation) and [Technology](../technology.md).

## Context
- A conversation can hold thousands of messages. Pages of 50 load as the reader scrolls up, but the list drew every
  loaded message, so the DOM grew with the history and every streamed piece rebuilt all the rows.
- `@angular/cdk` is not a dependency of the frontend. Its virtual scroll viewport wants a fixed item size or measures
  items with a strategy of its own, and a chat holds bubbles of any height, day separators, an editor that opens in
  place, a find bar that scrolls to a message by id, and a pinned bottom while a reply streams.
- Adding a package changes `package-lock.json` and the supply-chain surface for one component.

## Decision
- The conversation draws a window of its rows once it has `VIRTUAL_FROM` (200) of them. `virtual-window.ts` computes the
  window from the scroll position, the viewport height and the height of each row: the height measured when the row
  was drawn, or an estimate (96 px for a message, 48 px for a day separator) before. Two spacer items stand in for the
  rows above and below. Shorter lists are drawn whole.
- While the screen follows the bottom the window is the bottom. Loading an older page keeps the reader's place by
  distance from the bottom of the list. The find bar moves the window to the match before it scrolls to it.
- The gallery keeps paging (24 per page, a cursor) and lazy thumbnails (`loading="lazy"`, `content-visibility: auto`),
  which should be enough for fixed-ratio cells: the 1,000-image test checks that the browser fetches only the
  thumbnails near the viewport (written in P08.4, not run there).
- `@angular/cdk` stays a planned dependency for the overlay and focus trap only, if a later phase needs it.

## Alternatives
- **CDK `cdk-virtual-scroll-viewport` with the autosize strategy.** It is experimental, remeasures badly with an editor
  that opens in a row and with bubbles that grow while streaming, and adds a package.
- **Only `content-visibility: auto` on each row.** The browser skips painting, but the DOM and the rows' bindings still
  grow with the history.
- **Dropping old rows from the store.** It would make scrolling back reload them and lose edited and pending state.

## Consequences
- Rows outside the window are not in the DOM, so a screen reader, the browser's own find and Ctrl+F of the page see only
  the window of a long chat. The chat's own find bar (Ctrl+F) searches all loaded messages and scrolls to a match.
- The spacers rest on estimates for rows never drawn, so the scrollbar thumb can shift a little as rows are measured;
  the browser's scroll anchoring keeps the visible rows still.
- Measured heights are kept for the open chat only and are measured again when a row is drawn, so a change of width
  corrects itself for the rows in view.
- The behavior is covered by `virtual-window.spec.ts` and the end-to-end `performance.spec.mjs`.
