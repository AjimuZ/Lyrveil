// Focus helpers of the shell: where keyboard focus lands after a route change or a skip-link activation.

/**
 * Moves focus to the first `h1` inside `main`, or to `main` itself when the screen has none yet, and returns whether
 * something took the focus. A heading is not a control, so it receives `tabindex="-1"`: focusable by script, absent
 * from the Tab order. Scrolling is left alone because the shell already shows the top of a new screen.
 */
export function focusMainHeading(root: ParentNode = document): boolean {
  const target = root.querySelector<HTMLElement>('main h1') ?? root.querySelector<HTMLElement>('main');
  if (target === null) {
    return false;
  }
  if (!target.hasAttribute('tabindex')) {
    target.setAttribute('tabindex', '-1');
  }
  target.focus({ preventScroll: true });
  return true;
}

/** The path of a router URL, without its query string or fragment: a change of path is a change of screen. */
export function pathOf(url: string): string {
  return url.split(/[?#]/)[0] ?? url;
}
