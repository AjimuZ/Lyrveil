// Memory Studio state: the memories of one character with their filters, the actions on a memory, and the events,
// relationship and summaries of the chosen conversation.
import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient, ApiQuery } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { Conversation, ConversationList } from '../chats/chats.model';
import {
  Episode,
  MemoryChange,
  MemoryDetail,
  MemoryItem,
  MemoryKind,
  MemoryPage,
  MemoryProvenance,
  MemoryRevision,
  MemoryStatus,
  Relationship,
  StateChange,
  Summary,
} from './memory.model';

/** How long typing in the search box rests before the lists are requested again. */
export const MEMORY_SEARCH_DELAY_MS = 250;

/** `in_use` is what prompts use (active and uncertain); `all` asks for every status. */
export type StatusFilter = 'in_use' | 'all' | MemoryStatus;
export type LockFilter = 'all' | 'locked' | 'unlocked';

export interface MemoryFilters {
  readonly status: StatusFilter;
  readonly kind: MemoryKind | 'all';
  readonly provenance: MemoryProvenance | 'all';
  readonly lock: LockFilter;
}

export const DEFAULT_FILTERS: MemoryFilters = { status: 'in_use', kind: 'all', provenance: 'all', lock: 'all' };

/** The history and sources of one memory that the user opened. */
export interface OpenedMemory {
  readonly detail: MemoryDetail;
  readonly revisions: readonly MemoryRevision[];
}

/** One loadable part of the screen: its rows, whether the first answer is awaited, and the last failure. */
interface Part<T> {
  readonly data: T;
  readonly loading: boolean;
  readonly error: ApiError | null;
}

function part<T>(data: T, loading = false): Part<T> {
  return { data, loading, error: null };
}

/**
 * Holds what the Memory Studio shows. Reads are `Readonly` signals; every action calls the REST API and then reads
 * the list again, because the server is the source of truth (docs/frontend.md, State). A read that a newer one
 * overtook is dropped. A failed read keeps the previous rows and sets the part's `error`; a failed action sets
 * `actionError` and changes nothing.
 */
@Injectable({ providedIn: 'root' })
export class MemoryStudioStore {
  private readonly api = inject(ApiClient);
  private tickets = { memories: 0, episodes: 0, relationship: 0, summaries: 0 };
  private searchTimer: ReturnType<typeof setTimeout> | undefined;

  private readonly characterState = signal<string | null>(null);
  private readonly conversationState = signal<string | null>(null);
  private readonly conversationsState = signal<readonly Conversation[]>([]);
  private readonly filtersState = signal<MemoryFilters>(DEFAULT_FILTERS);
  private readonly queryState = signal('');
  private readonly cursorState = signal<string | null>(null);
  private readonly memoriesState = signal<Part<readonly MemoryItem[]>>(part([], true));
  private readonly episodesState = signal<Part<readonly Episode[]>>(part([], true));
  private readonly relationshipState = signal<Part<Relationship | null>>(part(null, true));
  private readonly historyState = signal<readonly StateChange[]>([]);
  private readonly summariesState = signal<Part<readonly Summary[]>>(part([], true));
  private readonly openedState = signal<Readonly<Record<string, OpenedMemory>>>({});
  private readonly actionErrorState = signal<ApiError | null>(null);

  /** The character being shown, or null before `open`. */
  readonly characterId = this.characterState.asReadonly();
  /** The chosen conversation, or null for all of the character's. */
  readonly conversationId = this.conversationState.asReadonly();
  readonly conversations = this.conversationsState.asReadonly();
  readonly filters = this.filtersState.asReadonly();
  readonly query = this.queryState.asReadonly();
  /** True while a next page of memories exists. */
  readonly hasMore = computed(() => this.cursorState() !== null);
  readonly memories = this.memoriesState.asReadonly();
  readonly episodes = this.episodesState.asReadonly();
  readonly relationship = this.relationshipState.asReadonly();
  readonly history = this.historyState.asReadonly();
  readonly summaries = this.summariesState.asReadonly();
  /** The memories whose sources and history are open, by id. */
  readonly opened = this.openedState.asReadonly();
  readonly actionError = this.actionErrorState.asReadonly();

  /**
   * Starts the screen for a character: forgets the previous character's rows, reads the character's active
   * conversations and the first page of memories. `conversationId` (from the link of a chat) chooses a conversation.
   */
  async open(characterId: string, conversationId: string | null): Promise<void> {
    clearTimeout(this.searchTimer);
    this.characterState.set(characterId);
    this.conversationState.set(conversationId);
    this.filtersState.set(DEFAULT_FILTERS);
    this.queryState.set('');
    this.openedState.set({});
    this.actionErrorState.set(null);
    this.memoriesState.set(part([], true));
    this.episodesState.set(part([], true));
    this.relationshipState.set(part(null, true));
    this.historyState.set([]);
    this.summariesState.set(part([], true));
    void this.loadConversations(characterId);
    await this.reloadMemories();
  }

  /** Chooses a conversation (null: all) and reads again what depends on it. */
  async selectConversation(conversationId: string | null): Promise<void> {
    this.conversationState.set(conversationId);
    this.episodesState.set(part([], true));
    this.relationshipState.set(part(null, true));
    this.historyState.set([]);
    this.summariesState.set(part([], true));
    await this.reloadMemories();
  }

  async setFilters(filters: Partial<MemoryFilters>): Promise<void> {
    this.filtersState.update((current) => ({ ...current, ...filters }));
    await this.reloadMemories();
  }

  /** Records the search text and reads the memories and episodes once typing has rested. */
  search(text: string): void {
    this.queryState.set(text);
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => {
      void this.reloadMemories();
      void this.loadEpisodes();
    }, MEMORY_SEARCH_DELAY_MS);
  }

  async reloadMemories(): Promise<void> {
    const characterId = this.characterState();
    if (characterId === null) {
      return;
    }
    const ticket = ++this.tickets.memories;
    this.memoriesState.update((current) => ({ ...current, loading: true }));
    try {
      const page = await firstValueFrom(this.api.get<MemoryPage>('/memories', this.memoryQuery(characterId, null)));
      if (ticket === this.tickets.memories) {
        this.memoriesState.set(part(page.items));
        this.cursorState.set(page.next_cursor);
      }
    } catch (failure) {
      if (ticket === this.tickets.memories) {
        this.memoriesState.update((current) => ({ ...current, loading: false, error: toApiError(failure) }));
      }
    }
  }

  /** Appends the next page of memories, if there is one. */
  async loadMore(): Promise<void> {
    const characterId = this.characterState();
    const cursor = this.cursorState();
    if (characterId === null || cursor === null) {
      return;
    }
    const ticket = ++this.tickets.memories;
    try {
      const page = await firstValueFrom(this.api.get<MemoryPage>('/memories', this.memoryQuery(characterId, cursor)));
      if (ticket === this.tickets.memories) {
        this.memoriesState.update((current) => part([...current.data, ...page.items]));
        this.cursorState.set(page.next_cursor);
      }
    } catch (failure) {
      if (ticket === this.tickets.memories) {
        this.memoriesState.update((current) => ({ ...current, error: toApiError(failure) }));
      }
    }
  }

  async loadEpisodes(): Promise<void> {
    const characterId = this.characterState();
    if (characterId === null) {
      return;
    }
    const ticket = ++this.tickets.episodes;
    try {
      const list = await firstValueFrom(
        this.api.get<{ items: readonly Episode[] }>('/episodes', {
          character_id: characterId,
          conversation_id: this.conversationState(),
          q: this.queryState().trim() || null,
        }),
      );
      if (ticket === this.tickets.episodes) {
        this.episodesState.set(part(list.items));
      }
    } catch (failure) {
      if (ticket === this.tickets.episodes) {
        this.episodesState.update((current) => ({ ...current, loading: false, error: toApiError(failure) }));
      }
    }
  }

  /** Reads the relationship state and its change log of the chosen conversation; does nothing without one. */
  async loadRelationship(): Promise<void> {
    const conversationId = this.conversationState();
    if (conversationId === null) {
      return;
    }
    const ticket = ++this.tickets.relationship;
    try {
      const [state, history] = await Promise.all([
        firstValueFrom(this.api.get<Relationship>(`/conversations/${conversationId}/relationship`)),
        firstValueFrom(
          this.api.get<{ items: readonly StateChange[] }>(`/conversations/${conversationId}/relationship/history`),
        ),
      ]);
      if (ticket === this.tickets.relationship) {
        this.relationshipState.set(part(state));
        this.historyState.set(history.items);
      }
    } catch (failure) {
      if (ticket === this.tickets.relationship) {
        this.relationshipState.update((current) => ({ ...current, loading: false, error: toApiError(failure) }));
      }
    }
  }

  /** Reads the summary checkpoints of the chosen conversation; does nothing without one. */
  async loadSummaries(): Promise<void> {
    const conversationId = this.conversationState();
    if (conversationId === null) {
      return;
    }
    const ticket = ++this.tickets.summaries;
    try {
      const list = await firstValueFrom(
        this.api.get<{ items: readonly Summary[] }>(`/conversations/${conversationId}/summaries`),
      );
      if (ticket === this.tickets.summaries) {
        this.summariesState.set(part(list.items));
      }
    } catch (failure) {
      if (ticket === this.tickets.summaries) {
        this.summariesState.update((current) => ({ ...current, loading: false, error: toApiError(failure) }));
      }
    }
  }

  /** Opens the sources, conflict group and history of a memory. */
  async openDetails(id: string): Promise<void> {
    this.actionErrorState.set(null);
    try {
      const [detail, revisions] = await Promise.all([
        firstValueFrom(this.api.get<MemoryDetail>(`/memories/${id}`)),
        firstValueFrom(this.api.get<{ items: readonly MemoryRevision[] }>(`/memories/${id}/revisions`)),
      ]);
      this.openedState.update((current) => ({ ...current, [id]: { detail, revisions: revisions.items } }));
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
    }
  }

  closeDetails(id: string): void {
    this.openedState.update((current) => Object.fromEntries(Object.entries(current).filter(([key]) => key !== id)));
  }

  /** Corrects the text or the importance. Returns whether the server accepted it; its reason is in `actionError`. */
  async edit(memory: MemoryItem, change: MemoryChange): Promise<boolean> {
    return this.act(memory.id, () => firstValueFrom(this.api.patch<MemoryDetail>(`/memories/${memory.id}`, change)));
  }

  async setLocked(memory: MemoryItem, locked: boolean): Promise<boolean> {
    const action = locked ? 'lock' : 'unlock';
    return this.act(memory.id, () => firstValueFrom(this.api.post<MemoryDetail>(`/memories/${memory.id}/${action}`)));
  }

  async confirm(memory: MemoryItem): Promise<boolean> {
    return this.act(memory.id, () => firstValueFrom(this.api.post<MemoryDetail>(`/memories/${memory.id}/confirm`)));
  }

  /** Deletes the memory; the server keeps only hashes, so the same message cannot bring it back. */
  async remove(memory: MemoryItem): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      await firstValueFrom(this.api.delete<null>(`/memories/${memory.id}`));
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
    this.closeDetails(memory.id);
    await this.reloadMemories();
    return true;
  }

  clearActionError(): void {
    this.actionErrorState.set(null);
  }

  private memoryQuery(characterId: string, cursor: string | null): ApiQuery {
    const filters = this.filtersState();
    const statuses: readonly string[] =
      filters.status === 'all' ? [] : filters.status === 'in_use' ? ['active', 'uncertain'] : [filters.status];
    return {
      character_id: characterId,
      conversation_id: this.conversationState(),
      kind: filters.kind === 'all' ? null : filters.kind,
      provenance: filters.provenance === 'all' ? null : filters.provenance,
      status: statuses,
      locked: filters.lock === 'all' ? null : filters.lock === 'locked',
      q: this.queryState().trim() || null,
      cursor,
    };
  }

  private async loadConversations(characterId: string): Promise<void> {
    try {
      const list = await firstValueFrom(this.api.get<ConversationList>('/conversations', { character_id: characterId }));
      if (this.characterState() === characterId) {
        this.conversationsState.set(list.items);
      }
    } catch {
      if (this.characterState() === characterId) {
        this.conversationsState.set([]);
      }
    }
  }

  /** Runs a change of one memory, then reads the list again and refreshes the memory if it is open. */
  private async act(id: string, work: () => Promise<unknown>): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      await work();
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
    await this.reloadMemories();
    if (id in this.openedState()) {
      await this.openDetails(id);
    }
    return true;
  }
}
