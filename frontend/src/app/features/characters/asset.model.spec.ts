import { squareCrop } from './asset.model';

describe('squareCrop', () => {
  it('takes the whole shorter side, centred, at full size', () => {
    expect(squareCrop(200, 100, 100, 50, 50)).toEqual({ x: 50, y: 0, width: 100, height: 100 });
  });

  it('scales the side and places the square within the room left', () => {
    expect(squareCrop(200, 100, 50, 100, 0)).toEqual({ x: 150, y: 0, width: 50, height: 50 });
  });

  it('stays inside the image for out-of-range controls', () => {
    const rect = squareCrop(40, 30, 500, -20, 300);
    expect(rect.x).toBeGreaterThanOrEqual(0);
    expect(rect.y + rect.height).toBeLessThanOrEqual(30);
    expect(rect.x + rect.width).toBeLessThanOrEqual(40);
  });
});
