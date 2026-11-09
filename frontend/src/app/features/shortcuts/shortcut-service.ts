// Global keyboard shortcuts of docs/frontend.md (Keyboard): one document listener that routes each key to its action.
import { Injectable, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { pathOf } from '../../core/shell/focus';
import { conversationIdOf, isTypingTarget, neighbourId, shortcutFor } from '../../core/shell/shortcuts';
import { ChatsStore } from '../chats/chats-store';

/** What the chat list screen has to do once it is on screen. */
export type ListRequest = 'search' | 'newChat';

/**
 * Turns the global shortcuts into actions. The root component forwards every `keydown` to `handle`.
 *
 * Ctrl+K and Ctrl+N have to act on the chat list, which may not be on screen: the service records a `pending` request,
 * opens `/chats` when needed, and the list screen performs the request and calls `consume`. Ctrl+F is taken only inside
 * a chat (`findRequests` counts the presses for the conversation screen); anywhere else the browser keeps its own find.
 * While a modal dialog is open no shortcut acts, so the page under it is never navigated by accident.
 */
@Injectable({ providedIn: 'root' })
export class ShortcutService {
  private readonly router = inject(Router);
  private readonly chats = inject(ChatsStore);

  /** The shortcut help dialog is open. */
  readonly helpOpen = signal(false);
  /** A request the chat list screen has not performed yet, or null. */
  readonly pending = signal<ListRequest | null>(null);
  /** Counts the Ctrl+F presses inside a chat: the conversation screen opens its find bar on each change. */
  readonly findRequests = signal(0);

  /** Runs the action of `event`, if it is a shortcut, and cancels the browser's own handling of it. */
  handle(event: KeyboardEvent): void {
    if (event.defaultPrevented) {
      return;
    }
    const action = shortcutFor(event, isTypingTarget(event.target));
    if (action === null || document.querySelector('dialog[open]') !== null) {
      return;
    }
    if (action === 'findInChat') {
      if (this.inChat(event.target)) {
        event.preventDefault();
        this.findRequests.update((count) => count + 1);
      }
      return;
    }
    event.preventDefault();
    switch (action) {
      case 'search':
      case 'newChat':
        void this.showList(action);
        break;
      case 'previousChat':
        void this.step(-1);
        break;
      case 'nextChat':
        void this.step(1);
        break;
      case 'help':
        this.helpOpen.set(true);
        break;
    }
  }

  /** Called by the chat list screen after it performed the pending request. */
  consume(): void {
    this.pending.set(null);
  }

  /** True when focus is in the conversation screen, or nowhere in particular while a chat is open. */
  private inChat(target: EventTarget | null): boolean {
    if (conversationIdOf(this.router.url) === null) {
      return false;
    }
    return target instanceof Element ? target.closest('lyr-conversation-screen') !== null || target === document.body : true;
  }

  private async showList(request: ListRequest): Promise<void> {
    this.pending.set(request);
    if (pathOf(this.router.url) !== '/chats' && !(await this.router.navigateByUrl('/chats'))) {
      // A guard kept the user where they were: the request must not fire later, on an unrelated visit to the list.
      this.pending.set(null);
    }
  }

  /** Opens the conversation `delta` rows away in the list on screen, if there is one. */
  private async step(delta: -1 | 1): Promise<void> {
    if (this.chats.items().length === 0) {
      await this.chats.reload();
    }
    const ids = this.chats.items().map((conversation) => conversation.id);
    const target = neighbourId(ids, conversationIdOf(this.router.url), delta);
    if (target !== null) {
      await this.router.navigate(['/chats', target]);
    }
  }
}
