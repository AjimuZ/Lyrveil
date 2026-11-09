// Which rows of a long list to draw: the window around the scroll position, with the height of what is left out.

/** The rows `start` up to (not including) `end` are drawn; `before` and `after` are the heights of the rows left out. */
export interface VirtualWindow {
  readonly start: number;
  readonly end: number;
  readonly before: number;
  readonly after: number;
  /** The height of the whole list, drawn or not. */
  readonly total: number;
}

/**
 * Returns the window of `count` rows to draw for a viewport of `viewport` pixels scrolled `scrollTop` pixels from the
 * top of the list, with `overscan` pixels more drawn on each side. `heightOf(i)` is the height of row `i`: measured
 * when the row was drawn once, an estimate otherwise. `scrollTop` is clamped to the list, so a stale value (the list
 * got shorter) still yields the last rows, and `Infinity` asks for the bottom.
 *
 * Always holds: `before + drawn heights + after === total`, `0 <= start <= end <= count`, and a non-empty list yields a
 * non-empty window. Runs in O(count); at 10,000 rows that is well below a frame.
 */
export function virtualWindow(
  count: number,
  heightOf: (index: number) => number,
  scrollTop: number,
  viewport: number,
  overscan: number,
): VirtualWindow {
  let total = 0;
  for (let index = 0; index < count; index += 1) {
    total += heightOf(index);
  }
  const top = Math.min(Math.max(scrollTop, 0), Math.max(total - viewport, 0));
  const from = top - overscan;
  const to = top + viewport + overscan;

  let start = count;
  let before = total;
  let end = count;
  let after = 0;
  let offset = 0;
  for (let index = 0; index < count; index += 1) {
    const height = heightOf(index);
    if (start === count && offset + height > from) {
      start = index;
      before = offset;
    }
    if (start < count && offset >= to) {
      end = index;
      after = total - offset;
      break;
    }
    offset += height;
  }
  if (start === count && count > 0) {
    // Every row ends above `from`, which the clamp makes impossible except through rounding: draw the last row.
    start = count - 1;
    before = total - heightOf(start);
  }
  if (end <= start && start < count) {
    // A zero-height viewport with no overscan: keep the row at the scroll position drawn.
    end = start + 1;
    after = total - before - heightOf(start);
  }
  return { start, end, before, after, total };
}

/** Returns the distance from the top of the list to the top of row `index`. */
export function offsetOf(index: number, heightOf: (index: number) => number): number {
  let offset = 0;
  for (let row = 0; row < index; row += 1) {
    offset += heightOf(row);
  }
  return offset;
}
