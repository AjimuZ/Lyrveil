// The text typed in a chat's composer and not sent yet, kept per chat in localStorage so a reload or a visit to another
// screen never loses it (docs/frontend.md, Sending).

const PREFIX = 'lyrveil.chat-draft.';

/** Returns the unsent text of the chat, or '' when there is none or the browser refuses storage. */
export function loadDraft(conversationId: string): string {
  try {
    return localStorage.getItem(PREFIX + conversationId) ?? '';
  } catch {
    return '';
  }
}

/** Keeps the unsent text of the chat; an empty text forgets it. A browser that refuses storage keeps nothing. */
export function saveDraft(conversationId: string, text: string): void {
  try {
    if (text === '') {
      localStorage.removeItem(PREFIX + conversationId);
    } else {
      localStorage.setItem(PREFIX + conversationId, text);
    }
  } catch {
    // The text stays in the composer for this visit; only the copy that survives a reload is lost.
  }
}
