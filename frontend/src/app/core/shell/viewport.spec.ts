import {
  MediaListLike,
  VIEWPORT_HEIGHT_PROPERTY,
  ViewportHost,
  followVisualViewport,
  isConversationUrl,
  matchesMedia,
  watchMedia,
} from './viewport';

/** A media query list that tests can flip. */
function fakeMedia(initial: boolean): MediaListLike & { flip(matches: boolean): void; listeners: number } {
  const listeners = new Set<(event: { matches: boolean }) => void>();
  const media = {
    matches: initial,
    addEventListener: (_type: 'change', listener: (event: { matches: boolean }) => void) => listeners.add(listener),
    removeEventListener: (_type: 'change', listener: (event: { matches: boolean }) => void) =>
      listeners.delete(listener),
    flip(matches: boolean) {
      media.matches = matches;
      listeners.forEach((listener) => listener({ matches }));
    },
    get listeners() {
      return listeners.size;
    },
  };
  return media;
}

describe('viewport helpers', () => {
  it('answers false for a query when the browser has no matchMedia', () => {
    expect(matchesMedia('(min-width: 1024px)', {})).toBe(false);
  });

  it('reports changes of a query until it is stopped', () => {
    const media = fakeMedia(false);
    const host: ViewportHost = { matchMedia: () => media };
    const seen: boolean[] = [];
    const stop = watchMedia('(min-width: 1024px)', (matches) => seen.push(matches), host);
    media.flip(true);
    media.flip(false);
    stop();
    media.flip(true);
    expect(seen).toEqual([true, false]);
    expect(media.listeners).toBe(0);
  });

  it('watches nothing when there is no matchMedia', () => {
    expect(() => watchMedia('(min-width: 1024px)', () => undefined, {})()).not.toThrow();
  });

  it('copies the visual viewport height into a custom property and removes it when stopped', () => {
    const handlers = new Map<string, () => void>();
    const viewport = {
      height: 412.4,
      addEventListener: (type: string, listener: () => void) => handlers.set(type, listener),
      removeEventListener: (type: string) => handlers.delete(type),
    };
    const root = document.createElement('div');
    const stop = followVisualViewport(root, { visualViewport: viewport });
    expect(root.style.getPropertyValue(VIEWPORT_HEIGHT_PROPERTY)).toBe('412px');

    viewport.height = 230;
    handlers.get('resize')?.();
    expect(root.style.getPropertyValue(VIEWPORT_HEIGHT_PROPERTY)).toBe('230px');

    stop();
    expect(root.style.getPropertyValue(VIEWPORT_HEIGHT_PROPERTY)).toBe('');
    expect(handlers.size).toBe(0);
  });

  it('sets nothing without a visual viewport', () => {
    const root = document.createElement('div');
    followVisualViewport(root, { visualViewport: null })();
    expect(root.style.getPropertyValue(VIEWPORT_HEIGHT_PROPERTY)).toBe('');
  });

  it('recognizes a conversation URL but not the chat list', () => {
    expect(isConversationUrl('/chats/3f2a')).toBe(true);
    expect(isConversationUrl('/chats/3f2a?x=1')).toBe(true);
    expect(isConversationUrl('/chats')).toBe(false);
    expect(isConversationUrl('/chats/')).toBe(false);
    expect(isConversationUrl('/characters/3f2a')).toBe(false);
  });
});
