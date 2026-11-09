// Viewport helpers of the responsive layout: breakpoint queries and the height that follows the on-screen keyboard.

/** The desktop layout starts here (docs/design-system.md, Layout). Below it the context panel opens as a dialog. */
export const WIDE_QUERY = '(min-width: 1024px)';

/** Below this width the mobile layout applies: bottom navigation, and the conversation takes the whole screen. */
export const NARROW_QUERY = '(max-width: 639px)';

/** The CSS custom property that carries the visible height; `app.scss` sizes the shell with it. */
export const VIEWPORT_HEIGHT_PROPERTY = '--ob-viewport-height';

/** The slice of `MediaQueryList` the helpers use. */
export interface MediaListLike {
  readonly matches: boolean;
  addEventListener(type: 'change', listener: (event: { matches: boolean }) => void): void;
  removeEventListener(type: 'change', listener: (event: { matches: boolean }) => void): void;
}

/** The slice of `window` the helpers use, so a test can pass a fake. */
export interface ViewportHost {
  matchMedia?: (query: string) => MediaListLike;
  visualViewport?: {
    height: number;
    addEventListener(type: string, listener: () => void): void;
    removeEventListener(type: string, listener: () => void): void;
  } | null;
}

/**
 * Returns whether `query` matches now. A browser (or test environment) without `matchMedia` answers false, which is
 * the narrow, single-column behavior: the safe one.
 */
export function matchesMedia(query: string, host: ViewportHost = window): boolean {
  return host.matchMedia?.(query).matches ?? false;
}

/**
 * Calls `onChange` with the new answer each time `query` starts or stops matching. Returns the function that stops
 * listening; without `matchMedia` nothing is observed and the function does nothing.
 */
export function watchMedia(query: string, onChange: (matches: boolean) => void, host: ViewportHost = window): () => void {
  const list = host.matchMedia?.(query);
  if (list === undefined) {
    return () => undefined;
  }
  const listener = (event: { matches: boolean }): void => onChange(event.matches);
  list.addEventListener('change', listener);
  return () => list.removeEventListener('change', listener);
}

/**
 * Keeps `--ob-viewport-height` on `root` equal to the height the user can see. On a phone the on-screen keyboard
 * shrinks the visual viewport but not always the layout viewport, so `100dvh` alone would leave the composer under the
 * keyboard; the shell uses this value first and `100dvh` when it is absent (docs/design-system.md, Mobile).
 * Returns the function that stops tracking and removes the property. Without `visualViewport` nothing is set.
 */
export function followVisualViewport(root: HTMLElement, host: ViewportHost = window): () => void {
  const viewport = host.visualViewport;
  if (viewport === undefined || viewport === null) {
    return () => undefined;
  }
  const update = (): void => root.style.setProperty(VIEWPORT_HEIGHT_PROPERTY, `${Math.round(viewport.height)}px`);
  update();
  viewport.addEventListener('resize', update);
  viewport.addEventListener('scroll', update);
  return () => {
    viewport.removeEventListener('resize', update);
    viewport.removeEventListener('scroll', update);
    root.style.removeProperty(VIEWPORT_HEIGHT_PROPERTY);
  };
}

/**
 * Whether the router URL is a conversation (`/chats/:id`). On the mobile layout the conversation shows no shell
 * chrome: its own header and composer fill the screen, and the bottom navigation is hidden.
 */
export function isConversationUrl(url: string): boolean {
  return /^\/chats\/[^/?#]+/.test(url);
}
