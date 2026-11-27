// Worlds list state: the worlds of a tab and a search, and the actions that create, archive and delete one.
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { BookFields, BookStatus, LoreBook, LoreBookList } from './lore.model';

/** How long typing in a search box rests before the list is requested again. */
export const LORE_SEARCH_DELAY_MS = 250;

/**
 * Holds what **Mondi** shows. Reads are `Readonly` signals; every action calls the REST API and then reloads the list,
 * because the server is the source of truth (docs/frontend.md, State). A reload that a newer one overtook is dropped. A
 * failed reload keeps the previous rows and sets `error`; a failed action sets `actionError` and changes nothing.
 */
@Injectable({ providedIn: 'root' })
export class WorldsStore {
  private readonly api = inject(ApiClient);
  private latestReload = 0;
  private searchTimer: ReturnType<typeof setTimeout> | undefined;

  private readonly itemsState = signal<readonly LoreBook[]>([]);
  private readonly loadingState = signal(true);
  private readonly errorState = signal<ApiError | null>(null);
  private readonly actionErrorState = signal<ApiError | null>(null);
  private readonly tabState = signal<BookStatus>('active');
  private readonly queryState = signal('');

  readonly items = this.itemsState.asReadonly();
  /** True until the first answer of the current tab and search arrives. */
  readonly loading = this.loadingState.asReadonly();
  readonly error = this.errorState.asReadonly();
  /** The failure of the last action, or null. */
  readonly actionError = this.actionErrorState.asReadonly();
  readonly tab = this.tabState.asReadonly();
  readonly query = this.queryState.asReadonly();

  /** Requests the worlds of the current tab and search, by name. */
  async reload(): Promise<void> {
    const ticket = ++this.latestReload;
    try {
      const list = await firstValueFrom(
        this.api.get<LoreBookList>('/lore/worlds', { status: this.tabState(), q: this.queryState().trim() || null }),
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

  async showTab(tab: BookStatus): Promise<void> {
    this.tabState.set(tab);
    this.loadingState.set(true);
    await this.reload();
  }

  /** Records the search text and reloads once typing has rested for LORE_SEARCH_DELAY_MS. */
  search(text: string): void {
    this.queryState.set(text);
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => void this.reload(), LORE_SEARCH_DELAY_MS);
  }

  /**
   * Creates a world and reloads the list. Returns it, or null after setting `actionError`; the server's reason, which
   * names the field, is in its `detail`.
   */
  async create(fields: BookFields): Promise<LoreBook | null> {
    this.actionErrorState.set(null);
    try {
      const created = await firstValueFrom(this.api.post<LoreBook>('/lore/worlds', fields));
      await this.reload();
      return created;
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return null;
    }
  }

  /** Archives an active world or brings an archived one back. */
  toggleArchive(world: LoreBook): Promise<boolean> {
    const action = world.status === 'archived' ? 'unarchive' : 'archive';
    return this.act(() => firstValueFrom(this.api.post<LoreBook>(`/lore/books/${world.id}/${action}`)));
  }

  /** Deletes a world with its entries and links. */
  remove(world: LoreBook): Promise<boolean> {
    return this.act(() => firstValueFrom(this.api.delete<null>(`/lore/books/${world.id}`)));
  }

  clearActionError(): void {
    this.actionErrorState.set(null);
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
