// State of one open book: its settings, its entries and the actions on both.
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { BookFields, EntryFields, LoreBook, LoreEntry, LoreEntryList } from './lore.model';
import { LORE_SEARCH_DELAY_MS } from './worlds-store';

/** The fields of an entry as they are now, with `enabled` replaced; a PUT replaces every field, so all are sent. */
export function fieldsWithEnabled(entry: LoreEntry, enabled: boolean): EntryFields {
  return {
    title: entry.title,
    content: entry.content,
    keys: entry.keys,
    secondary_keys: entry.secondary_keys,
    exclude_keys: entry.exclude_keys,
    case_sensitive: entry.case_sensitive,
    constant: entry.constant,
    enabled,
    priority: entry.priority,
    insertion_order: entry.insertion_order,
    note: entry.note,
  };
}

/**
 * Holds what the editor of one book shows. It is provided by the editor component, so each open book has its own. Reads
 * are `Readonly` signals; every action calls the REST API and then reads again, because the server is the source of
 * truth (docs/frontend.md, State). A failed save sets `bookError` or `entryError` with the server's refusal and changes
 * nothing; any other failed action sets `actionError`.
 */
@Injectable()
export class BookEditorStore {
  private readonly api = inject(ApiClient);
  private latestEntries = 0;
  private searchTimer: ReturnType<typeof setTimeout> | undefined;
  private bookId: string | null = null;

  private readonly bookState = signal<LoreBook | null>(null);
  private readonly entriesState = signal<readonly LoreEntry[]>([]);
  private readonly loadingState = signal(true);
  private readonly errorState = signal<ApiError | null>(null);
  private readonly bookErrorState = signal<ApiError | null>(null);
  private readonly entryErrorState = signal<ApiError | null>(null);
  private readonly actionErrorState = signal<ApiError | null>(null);
  private readonly queryState = signal('');

  readonly book = this.bookState.asReadonly();
  readonly entries = this.entriesState.asReadonly();
  /** True until the book and its entries first arrive. */
  readonly loading = this.loadingState.asReadonly();
  /** The failure of the first read, or null. */
  readonly error = this.errorState.asReadonly();
  readonly bookError = this.bookErrorState.asReadonly();
  readonly entryError = this.entryErrorState.asReadonly();
  readonly actionError = this.actionErrorState.asReadonly();
  readonly query = this.queryState.asReadonly();

  /** Reads the book and its entries. Sets `error` when either read fails. */
  async load(bookId: string): Promise<void> {
    this.bookId = bookId;
    this.loadingState.set(true);
    try {
      const [book, entries] = await Promise.all([
        firstValueFrom(this.api.get<LoreBook>(`/lore/books/${bookId}`)),
        firstValueFrom(this.api.get<LoreEntryList>(`/lore/books/${bookId}/entries`, this.entryQuery())),
      ]);
      this.bookState.set(book);
      this.entriesState.set(entries.items);
      this.errorState.set(null);
    } catch (failure) {
      this.errorState.set(toApiError(failure));
    } finally {
      this.loadingState.set(false);
    }
  }

  /** Records the search text and reads the entries again once typing has rested for LORE_SEARCH_DELAY_MS. */
  search(text: string): void {
    this.queryState.set(text);
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => void this.reloadEntries(), LORE_SEARCH_DELAY_MS);
  }

  /** Replaces the book's settings. Returns the saved book, or null after setting `bookError`. */
  async saveBook(fields: BookFields): Promise<LoreBook | null> {
    const id = this.bookId;
    this.bookErrorState.set(null);
    if (id === null) {
      return null;
    }
    try {
      const saved = await firstValueFrom(this.api.put<LoreBook>(`/lore/books/${id}`, fields));
      this.bookState.set(saved);
      return saved;
    } catch (failure) {
      this.bookErrorState.set(toApiError(failure));
      return null;
    }
  }

  /** Archives or restores the world. Returns whether the server did it. */
  async setArchived(archived: boolean): Promise<boolean> {
    const id = this.bookId;
    if (id === null) {
      return false;
    }
    return this.act(async () => {
      const book = await firstValueFrom(this.api.post<LoreBook>(`/lore/books/${id}/${archived ? 'archive' : 'unarchive'}`));
      this.bookState.set(book);
    });
  }

  /** Deletes the world with its entries and links. Returns whether the server did it. */
  async removeBook(): Promise<boolean> {
    const id = this.bookId;
    if (id === null) {
      return false;
    }
    return this.act(async () => {
      await firstValueFrom(this.api.delete<null>(`/lore/books/${id}`));
    });
  }

  /**
   * Creates the entry, or replaces the fields of the one named by `id`, and reads the entries again. Returns the saved
   * entry, which says through `never_inserted` whether any reply will ever read it, or null after setting `entryError`.
   */
  async saveEntry(id: string | null, fields: EntryFields): Promise<LoreEntry | null> {
    const book = this.bookId;
    this.entryErrorState.set(null);
    if (book === null) {
      return null;
    }
    try {
      const saved = await firstValueFrom(
        id === null
          ? this.api.post<LoreEntry>(`/lore/books/${book}/entries`, fields)
          : this.api.put<LoreEntry>(`/lore/entries/${id}`, fields),
      );
      await this.refresh();
      return saved;
    } catch (failure) {
      this.entryErrorState.set(toApiError(failure));
      return null;
    }
  }

  /** Switches an entry on or off without opening it. */
  toggleEnabled(entry: LoreEntry): Promise<boolean> {
    return this.act(async () => {
      await firstValueFrom(this.api.put<LoreEntry>(`/lore/entries/${entry.id}`, fieldsWithEnabled(entry, !entry.enabled)));
      await this.refresh();
    });
  }

  removeEntry(entry: LoreEntry): Promise<boolean> {
    return this.act(async () => {
      await firstValueFrom(this.api.delete<null>(`/lore/entries/${entry.id}`));
      await this.refresh();
    });
  }

  clearErrors(): void {
    this.bookErrorState.set(null);
    this.entryErrorState.set(null);
    this.actionErrorState.set(null);
  }

  clearEntryError(): void {
    this.entryErrorState.set(null);
  }

  /** Reads the entries and the book again, so the entry count follows. A failure keeps what is shown. */
  private async refresh(): Promise<void> {
    const id = this.bookId;
    if (id === null) {
      return;
    }
    await this.reloadEntries();
    try {
      this.bookState.set(await firstValueFrom(this.api.get<LoreBook>(`/lore/books/${id}`)));
    } catch {
      // The count may be stale until the next read; the entries above are current.
    }
  }

  private async reloadEntries(): Promise<void> {
    const id = this.bookId;
    if (id === null) {
      return;
    }
    const ticket = ++this.latestEntries;
    try {
      const list = await firstValueFrom(this.api.get<LoreEntryList>(`/lore/books/${id}/entries`, this.entryQuery()));
      if (ticket === this.latestEntries) {
        this.entriesState.set(list.items);
      }
    } catch (failure) {
      if (ticket === this.latestEntries) {
        this.actionErrorState.set(toApiError(failure));
      }
    }
  }

  private entryQuery(): { q: string | null } {
    return { q: this.queryState().trim() || null };
  }

  private async act(work: () => Promise<void>): Promise<boolean> {
    this.actionErrorState.set(null);
    try {
      await work();
      return true;
    } catch (failure) {
      this.actionErrorState.set(toApiError(failure));
      return false;
    }
  }
}
