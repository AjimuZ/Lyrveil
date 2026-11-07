// Gallery screen state: the pages of images for the current filters, and the delete and regenerate actions.
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { Character, CharacterList } from '../chats/chats.model';
import { GalleryFilters, GalleryImage, GalleryPage, NO_FILTERS } from './gallery.model';

/** How many images one request asks for. */
export const GALLERY_PAGE_SIZE = 24;

/**
 * Holds what the gallery shows. Reads are `Readonly` signals. `load` replaces the rows with the first page of the
 * filters; `loadMore` appends the next one. A response that a newer `load` overtook is dropped. A failed load keeps the
 * previous rows and sets `error`; a failed action sets `actionError` and changes nothing.
 */
@Injectable({ providedIn: 'root' })
export class GalleryStore {
  private readonly api = inject(ApiClient);
  private latestLoad = 0;

  private readonly itemsState = signal<readonly GalleryImage[]>([]);
  private readonly cursorState = signal<string | null>(null);
  private readonly loadingState = signal(true);
  private readonly loadingMoreState = signal(false);
  private readonly errorState = signal<ApiError | null>(null);
  private readonly actionErrorState = signal<ApiError | null>(null);
  private readonly filtersState = signal<GalleryFilters>(NO_FILTERS);

  readonly items = this.itemsState.asReadonly();
  /** True while there is a next page to request. */
  readonly hasMore = () => this.cursorState() !== null;
  /** True until the first page of the current filters arrives. */
  readonly loading = this.loadingState.asReadonly();
  readonly loadingMore = this.loadingMoreState.asReadonly();
  readonly error = this.errorState.asReadonly();
  /** The failure of the last action, or null. */
  readonly actionError = this.actionErrorState.asReadonly();
  readonly filters = this.filtersState.asReadonly();

  /** Requests the first page for `filters` (the current ones when omitted). */
  async load(filters: GalleryFilters = this.filtersState()): Promise<void> {
    const ticket = ++this.latestLoad;
    this.filtersState.set(filters);
    this.loadingState.set(true);
    try {
      const page = await this.fetch(filters, null);
      if (ticket === this.latestLoad) {
        this.itemsState.set(page.items);
        this.cursorState.set(page.next_cursor);
        this.errorState.set(null);
      }
    } catch (failure) {
      if (ticket === this.latestLoad) {
        this.errorState.set(toApiError(failure));
      }
    } finally {
      if (ticket === this.latestLoad) {
        this.loadingState.set(false);
      }
    }
  }

  /** Appends the next page. Does nothing without a next page or while one is on its way. */
  async loadMore(): Promise<void> {
    const cursor = this.cursorState();
    if (cursor === null || this.loadingMoreState()) {
      return;
    }
    const ticket = this.latestLoad;
    this.loadingMoreState.set(true);
    try {
      const page = await this.fetch(this.filtersState(), cursor);
      if (ticket === this.latestLoad) {
        const known = new Set(this.itemsState().map((image) => image.id));
        this.itemsState.update((rows) => [...rows, ...page.items.filter((image) => !known.has(image.id))]);
        this.cursorState.set(page.next_cursor);
        this.errorState.set(null);
      }
    } catch (failure) {
      if (ticket === this.latestLoad) {
        this.errorState.set(toApiError(failure));
      }
    } finally {
      this.loadingMoreState.set(false);
    }
  }

  /** Deletes the image on the server and removes it from the rows. Returns false after setting `actionError`. */
  async remove(image: GalleryImage): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      await firstValueFrom(this.api.delete<null>(`/images/${image.id}`));
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
    this.itemsState.update((rows) => rows.filter((row) => row.id !== image.id));
    return true;
  }

  /**
   * Queues a new drawing of the image as a variant of its message. Returns true once the server accepted it; the
   * new image appears in the gallery when the job ends. Returns false after setting `actionError`.
   */
  async regenerate(image: GalleryImage, keepSeed: boolean): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      await firstValueFrom(this.api.post<unknown>(`/images/${image.id}/regenerate`, { keep_seed: keepSeed }));
      return true;
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
  }

  /** Returns the characters, for the filter. Rejects with an ApiError. */
  async listCharacters(): Promise<readonly Character[]> {
    const list = await firstValueFrom(this.api.get<CharacterList>('/characters'));
    return list.items;
  }

  clearActionError(): void {
    this.actionErrorState.set(null);
  }

  private fetch(filters: GalleryFilters, cursor: string | null): Promise<GalleryPage> {
    return firstValueFrom(
      this.api.get<GalleryPage>('/images', {
        character_id: filters.characterId || null,
        purpose: filters.purpose || null,
        cursor,
        limit: GALLERY_PAGE_SIZE,
      }),
    );
  }
}
