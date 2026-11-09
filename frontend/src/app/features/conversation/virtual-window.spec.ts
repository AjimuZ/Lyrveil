import { offsetOf, virtualWindow } from './virtual-window';

describe('virtual window', () => {
  const fixed = (): number => 100;

  it('draws the rows around the scroll position and measures what it leaves out', () => {
    // 1000 rows of 100 px, viewport 500 px at 20,000 px, 200 px of overscan: rows 198 to 207 (19,800 to 20,700).
    const window = virtualWindow(1000, fixed, 20_000, 500, 200);
    expect(window.start).toBe(198);
    expect(window.end).toBe(207);
    expect(window.before).toBe(19_800);
    expect(window.after).toBe(100_000 - 20_700);
    expect(window.total).toBe(100_000);
  });

  it('keeps before, drawn rows and after equal to the total with rows of different heights', () => {
    const heightOf = (index: number): number => 40 + (index % 7) * 13;
    for (const scrollTop of [0, 1, 777, 12_345, 1e9, Infinity]) {
      const window = virtualWindow(500, heightOf, scrollTop, 640, 300);
      let drawn = 0;
      for (let index = window.start; index < window.end; index += 1) {
        drawn += heightOf(index);
      }
      expect(window.before + drawn + window.after).toBe(window.total);
      expect(window.end).toBeGreaterThan(window.start);
      expect(window.end - window.start).toBeLessThan(40);
    }
  });

  it('asks for the bottom with an infinite scroll position and clamps a stale one', () => {
    const bottom = virtualWindow(1000, fixed, Infinity, 500, 0);
    expect(bottom.end).toBe(1000);
    expect(bottom.after).toBe(0);
    expect(virtualWindow(1000, fixed, 5e8, 500, 0)).toEqual(bottom);
    expect(virtualWindow(1000, fixed, -50, 500, 0).start).toBe(0);
  });

  it('handles an empty list and a zero-height viewport', () => {
    expect(virtualWindow(0, fixed, 0, 500, 100)).toEqual({ start: 0, end: 0, before: 0, after: 0, total: 0 });
    const window = virtualWindow(10, fixed, 300, 0, 0);
    expect(window.end - window.start).toBe(1);
  });

  it('gives the offset of a row from the top of the list', () => {
    expect(offsetOf(0, fixed)).toBe(0);
    expect(offsetOf(25, fixed)).toBe(2500);
  });
});
