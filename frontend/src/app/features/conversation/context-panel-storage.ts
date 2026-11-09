// Whether the desktop context panel of the conversation is collapsed, remembered per browser in localStorage
// (docs/design-system.md, Layout; docs/frontend.md, Persistence).

const KEY = 'lyrveil.context-panel';

/** Returns true when the user collapsed the panel. A missing value or a browser that refuses storage means open. */
export function loadPanelCollapsed(): boolean {
  try {
    return localStorage.getItem(KEY) === 'collapsed';
  } catch {
    return false;
  }
}

/** Keeps the choice; a browser that refuses storage keeps it for this visit only. */
export function savePanelCollapsed(collapsed: boolean): void {
  try {
    if (collapsed) {
      localStorage.setItem(KEY, 'collapsed');
    } else {
      localStorage.removeItem(KEY);
    }
  } catch {
    // The choice holds until the page is closed; only its memory across visits is lost.
  }
}
