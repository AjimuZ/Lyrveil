// Conversation list state: the list for a tab and a search, the actions on a row, and the characters a chat starts from.
import { Injectable, inject, signal } from '@angular/core';
import { Subscription, firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { DataEventType, EventsSocket } from '../../core/realtime/events-socket';
import {
  Character,
  CharacterList,
  Conversation,
  ConversationList,
  ConversationStatus,
  NewCharacter,
} from './chats.model';

/** How long typing in the search box rests before the list is requested again. */
export const SEARCH_DELAY_MS = 250;

/** How long the live list waits after an event before it asks again, so a burst of events costs one request. */
export const LIVE_DELAY_MS = 300;

/** The events that change a row: its preview, unread count or what the character is doing. Deltas are left out. */
const ROW_EVENTS: readonly DataEventType[] = [
  'message.created',
  'message.updated',
  'message.completed',
  'message.failed',
  'message.deleted',
  'typing.started',
  'typing.stopped',
  'image.completed',
];

/**
 * Holds what the conversation list shows. Reads are `Readonly` signals; every method is an intent that calls the
 * REST API and then reloads the list, because the server is the source of truth (docs/frontend.md, State).
 *
 * A reload that is overtaken by a newer one is dropped, so a slow answer never replaces a fresher list. A failed
 * reload keeps the previous rows and sets `error`; a failed action sets `actionError` and changes nothing.
 *
 * While `follow` is active the rows stay live: a message or typing event, or a resync, reloads the list after
 * LIVE_DELAY_MS, which updates previews, unread counts and the typing, drawing and queue hints.
 */
@Injectable({ providedIn: 'root' })
export class ChatsStore {
  private readonly api = inject(ApiClient);
  private readonly socket = inject(EventsSocket);
  private latestReload = 0;
  private searchTimer: ReturnType<typeof setTimeout> | undefined;
  private liveTimer: ReturnType<typeof setTimeout> | undefined;
  private followers: Subscription[] = [];

  private readonly itemsState = signal<readonly Conversation[]>([]);
  private readonly loadingState = signal(true);
  private readonly errorState = signal<ApiError | null>(null);
  private readonly actionErrorState = signal<ApiError | null>(null);
  private readonly tabState = signal<ConversationStatus>('active');
  private readonly queryState = signal('');
  private readonly deletedState = signal<Conversation | null>(null);

  readonly items = this.itemsState.asReadonly();
  /** True until the first answer of the current tab and search arrives. */
  readonly loading = this.loadingState.asReadonly();
  /** The failure of the last reload, or null. */
  readonly error = this.errorState.asReadonly();
  /** The failure of the last row action or creation, or null. */
  readonly actionError = this.actionErrorState.asReadonly();
  readonly tab = this.tabState.asReadonly();
  readonly query = this.queryState.asReadonly();
  /** The conversation the user just deleted, kept so the screen can offer to restore it. */
  readonly justDeleted = this.deletedState.asReadonly();

  /** Requests the list for the current tab and search. */
  async reload(): Promise<void> {
    const ticket = ++this.latestReload;
    try {
      const list = await firstValueFrom(
        this.api.get<ConversationList>('/conversations', { status: this.tabState(), q: this.queryState().trim() || null }),
      );
      if (ticket === this.latestReload) {
        this.itemsState.set(list.items);
        this.errorState.set(null);
      }
    } catch (failure) {
      if (ticket === this.latestReload) {
        this.errorState.set(toApiError(failure));
      }
    } finally {
      if (ticket === this.latestReload) {
        this.loadingState.set(false);
      }
    }
  }

  /** Connects the event socket and keeps the rows live until `unfollow`. Does nothing while already following. */
  follow(): void {
    if (this.followers.length > 0) {
      return;
    }
    this.socket.connect();
    this.followers = [
      ...ROW_EVENTS.map((type) => this.socket.on(type).subscribe(() => this.reloadSoon())),
      this.socket.resync$.subscribe(() => this.reloadSoon()),
    ];
  }

  /** Stops following events. The rows on hand stay. */
  unfollow(): void {
    for (const follower of this.followers) {
      follower.unsubscribe();
    }
    this.followers = [];
    clearTimeout(this.liveTimer);
    this.liveTimer = undefined;
  }

  private reloadSoon(): void {
    if (this.liveTimer !== undefined) {
      return;
    }
    this.liveTimer = setTimeout(() => {
      this.liveTimer = undefined;
      void this.reload();
    }, LIVE_DELAY_MS);
  }

  /** Switches between the active and the archived chats and reloads. */
  async showTab(tab: ConversationStatus): Promise<void> {
    this.tabState.set(tab);
    this.deletedState.set(null);
    this.loadingState.set(true);
    await this.reload();
  }

  /** Records the search text and reloads once typing has rested for SEARCH_DELAY_MS. */
  search(text: string): void {
    this.queryState.set(text);
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => void this.reload(), SEARCH_DELAY_MS);
  }

  async togglePin(conversation: Conversation): Promise<boolean> {
    return this.act(() => this.patch(conversation, { pinned: !conversation.pinned }));
  }

  /** Archives an active chat or brings an archived one back. */
  async toggleArchive(conversation: Conversation): Promise<boolean> {
    return this.act(() => this.patch(conversation, { archived: conversation.status !== 'archived' }));
  }

  /** Deletes the chat (it can be restored for 30 days) and remembers it in `justDeleted`. */
  async remove(conversation: Conversation): Promise<boolean> {
    const done = await this.act(() => firstValueFrom(this.api.delete<null>(`/conversations/${conversation.id}`)));
    if (done) {
      this.deletedState.set(conversation);
    }
    return done;
  }

  /** Restores the chat in `justDeleted`; it comes back active. */
  async restoreDeleted(): Promise<boolean> {
    const deleted = this.deletedState();
    if (deleted === null) {
      return false;
    }
    const done = await this.act(() => firstValueFrom(this.api.post<Conversation>(`/conversations/${deleted.id}/undelete`)));
    if (done) {
      this.deletedState.set(null);
    }
    return done;
  }

  dismissDeleted(): void {
    this.deletedState.set(null);
  }

  /** Returns the active characters, newest first. Rejects with an ApiError. */
  async listCharacters(): Promise<readonly Character[]> {
    const list = await firstValueFrom(this.api.get<CharacterList>('/characters'));
    return list.items;
  }

  /** Creates a character from the minimal form. Rejects with an ApiError, whose `detail` explains a refusal. */
  async createCharacter(values: NewCharacter): Promise<Character> {
    return firstValueFrom(this.api.post<Character>('/characters', values));
  }

  /**
   * Starts a chat with the character in `language` (null follows the scenario, else the character), optionally from
   * the scenario `scenarioId`, and switches to the active tab so the new row is visible. A character may have any
   * number of chats. Rejects with an ApiError.
   */
  async startChat(
    characterId: string,
    language: string | null,
    scenarioId: string | null = null,
  ): Promise<Conversation> {
    const created = await firstValueFrom(
      this.api.post<Conversation>('/conversations', {
        character_id: characterId,
        language,
        scenario_id: scenarioId,
      }),
    );
    this.queryState.set('');
    this.deletedState.set(null);
    await this.showTab('active');
    return created;
  }

  private patch(conversation: Conversation, changes: Record<string, boolean>): Promise<Conversation> {
    return firstValueFrom(this.api.patch<Conversation>(`/conversations/${conversation.id}`, changes));
  }

  private async act(work: () => Promise<unknown>): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      await work();
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
    await this.reload();
    return true;
  }
}
