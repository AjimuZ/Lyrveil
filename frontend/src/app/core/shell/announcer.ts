// Announcer: the text of the shell's polite live region, for events that no visible element reports by itself.
import { Injectable, signal } from '@angular/core';

/** How long the region stays empty before the new text, so a repeated sentence is announced again. */
const RESET_MS = 60;

/**
 * Screens call `announce` when something finishes that a screen reader user would otherwise miss, such as a completed
 * reply (docs/design-system.md, Accessibility: live regions). The root component renders `message` in a
 * `aria-live="polite"` region. Streaming text must never be announced here piece by piece: announce once, when done.
 */
@Injectable({ providedIn: 'root' })
export class Announcer {
  private readonly state = signal('');
  private timer: ReturnType<typeof setTimeout> | undefined;

  /** The sentence the live region currently holds; empty between announcements. */
  readonly message = this.state.asReadonly();

  /** Replaces the live region's text with `text`. The latest call wins; an empty text only clears the region. */
  announce(text: string): void {
    clearTimeout(this.timer);
    this.state.set('');
    if (text === '') {
      return;
    }
    this.timer = setTimeout(() => this.state.set(text), RESET_MS);
  }
}
