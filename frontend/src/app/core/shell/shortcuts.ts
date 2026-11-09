// Keyboard shortcuts of docs/frontend.md (Keyboard): which key combination means which action, as pure functions.

/** What a global shortcut asks for. Enter, Shift+Enter, Esc and the arrows of the lightbox belong to their controls. */
export type ShortcutAction = 'search' | 'newChat' | 'previousChat' | 'nextChat' | 'findInChat' | 'help';

/** The slice of `KeyboardEvent` that decides a shortcut, so a test can pass a plain object. */
export interface KeyEventLike {
  readonly key: string;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  readonly altKey: boolean;
  readonly shiftKey: boolean;
  readonly isComposing?: boolean;
}

/**
 * Returns the action `event` asks for, or null when it is not one of ours. `typing` says that focus is in a field where
 * a character key must reach the field, which is why `?` opens the help only outside of one; the combinations with
 * Ctrl (or Cmd) or Alt work everywhere. Combinations carrying an extra modifier return null, so the browser's own
 * shortcuts such as Ctrl+Shift+N stay intact.
 */
export function shortcutFor(event: KeyEventLike, typing: boolean): ShortcutAction | null {
  if (event.isComposing === true) {
    return null;
  }
  const primary = event.ctrlKey || event.metaKey;
  if (primary && !event.altKey && !event.shiftKey) {
    switch (event.key.toLowerCase()) {
      case 'k':
        return 'search';
      case 'n':
        return 'newChat';
      case 'f':
        return 'findInChat';
      default:
        return null;
    }
  }
  if (event.altKey && !primary && !event.shiftKey) {
    if (event.key === 'ArrowUp') {
      return 'previousChat';
    }
    if (event.key === 'ArrowDown') {
      return 'nextChat';
    }
    return null;
  }
  if (event.key === '?' && !primary && !event.altKey && !typing) {
    return 'help';
  }
  return null;
}

/** Whether `target` takes typed characters: a text field, a select or an editable element. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.isContentEditable) {
    return true;
  }
  if (target instanceof HTMLInputElement) {
    return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'file'].includes(target.type);
  }
  return target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
}

/**
 * Returns the id next to `currentId` in `ids`, `delta` places away (-1 is the previous row, 1 the next), or null at
 * either end of the list. When `currentId` is not in the list (the list screen, or a chat on another tab), the first
 * row answers `1` and the last answers `-1`; an empty list always answers null.
 */
export function neighbourId(ids: readonly string[], currentId: string | null, delta: -1 | 1): string | null {
  if (ids.length === 0) {
    return null;
  }
  const index = currentId === null ? -1 : ids.indexOf(currentId);
  if (index === -1) {
    return (delta === 1 ? ids[0] : ids[ids.length - 1]) ?? null;
  }
  return ids[index + delta] ?? null;
}

/** The id in a conversation URL (`/chats/:id`), or null on any other URL. */
export function conversationIdOf(url: string): string | null {
  return /^\/chats\/([^/?#]+)/.exec(url)?.[1] ?? null;
}
