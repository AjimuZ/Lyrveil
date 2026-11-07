// The local draft of a new character: the Creator's model kept in the browser until the character is saved.
import { CreatorModel } from './card-form';

/** The key holds one draft: the form of a new character. Editing a stored character never writes a draft. */
export const DRAFT_KEY = 'lyrveil.character-draft';
const DRAFT_VERSION = 1;

/** Returns the stored draft, or null when there is none, it is unreadable, or another version of the form wrote it. */
export function readDraft(): CreatorModel | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (raw === null) {
      return null;
    }
    const stored = JSON.parse(raw) as { version?: unknown; model?: unknown };
    return stored.version === DRAFT_VERSION && typeof stored.model === 'object' && stored.model !== null
      ? (stored.model as CreatorModel)
      : null;
  } catch {
    return null;
  }
}

/** Stores the draft. A browser that refuses storage (private mode, full quota) loses the draft, never the form. */
export function writeDraft(model: CreatorModel): void {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ version: DRAFT_VERSION, model }));
  } catch {
    // The draft is a convenience; the form keeps working without it.
  }
}

export function clearDraft(): void {
  try {
    localStorage.removeItem(DRAFT_KEY);
  } catch {
    // Nothing to clear when storage is unavailable.
  }
}
