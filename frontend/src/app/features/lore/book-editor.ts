// The editor of one lore book, a world or a character's own: its settings and its entries.
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, input, output, signal } from '@angular/core';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { Translator } from '../../core/i18n/translator';
import { ObEmptyState } from '../../shared/ui/empty-state';
import { ObSkeleton } from '../../shared/ui/skeleton';
import { BookEditorStore } from './book-editor-store';
import { EntryForm } from './entry-form';
import { BookField, EntryFields, LORE_LIMITS, LoreBook, LoreEntry, bookFieldOf } from './lore.model';

/**
 * The editor of the book named by `bookId`. A world has its **Nome**, **Descrizione**, **Messaggi da leggere** and
 * **Budget di token** (blank means the block's default), and can be archived, restored and deleted; the own book of a
 * character has only the last two settings, and goes only with its character. Below, the entries: a search over title,
 * content, keys and note, **Nuova voce**, and per entry an edit, an **Attiva** switch and a delete with an inline
 * confirmation. Each refusal of the server is shown next to the field it names. An entry too long to ever enter a
 * prompt is marked in its row and announced when it is saved, because it is stored but no reply reads it.
 *
 * `removed` fires after the server deleted the world, so the screen can leave.
 */
@Component({
  selector: 'lyr-book-editor',
  imports: [TranslatePipe, EntryForm, ObEmptyState, ObSkeleton],
  providers: [BookEditorStore],
  templateUrl: './book-editor.html',
  styleUrl: './book-editor.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BookEditor implements OnInit {
  protected readonly store = inject(BookEditorStore);
  private readonly translator = inject(Translator);

  readonly bookId = input.required<string>();
  readonly removed = output();

  protected readonly limits = LORE_LIMITS;

  protected readonly name = signal('');
  protected readonly description = signal('');
  protected readonly scanDepth = signal(String(LORE_LIMITS.defaultScanDepth));
  protected readonly tokenBudget = signal('');
  protected readonly attempted = signal(false);
  protected readonly savingBook = signal(false);
  protected readonly bookSaved = signal(false);
  protected readonly confirmingBookDelete = signal(false);

  /** `'new'`, the id of the entry being edited, or null when the form is closed. */
  protected readonly editing = signal<string | null>(null);
  protected readonly savingEntry = signal(false);
  protected readonly confirmingDelete = signal<string | null>(null);
  /** The title of the entry that was just saved too long to ever be inserted, or null. */
  protected readonly tooLongTitle = signal<string | null>(null);

  protected readonly isWorld = computed(() => this.store.book()?.kind === 'world');
  protected readonly archived = computed(() => this.store.book()?.status === 'archived');
  /** One item while the form is open, so a change of target builds a new form with its own fields. */
  protected readonly formTarget = computed(() => {
    const target = this.editing();
    return target === null ? [] : [target];
  });

  protected readonly nameValid = computed(() => {
    const length = this.name().trim().length;
    return length >= 1 && length <= LORE_LIMITS.name;
  });
  protected readonly descriptionValid = computed(() => this.description().trim().length <= LORE_LIMITS.description);
  protected readonly scanDepthValid = computed(() => {
    const text = this.scanDepth().trim();
    const value = Number(text);
    return /^\d+$/.test(text) && value >= LORE_LIMITS.minScanDepth && value <= LORE_LIMITS.maxScanDepth;
  });
  /** The budget is optional: blank is valid, otherwise a whole number from 50 to 900. */
  protected readonly tokenBudgetValid = computed(() => {
    const text = this.tokenBudget().trim();
    if (text === '') {
      return true;
    }
    const value = Number(text);
    return /^\d+$/.test(text) && value >= LORE_LIMITS.minBudget && value <= LORE_LIMITS.maxBudget;
  });

  /** The field of the settings form that the server's last refusal named, or null. */
  protected readonly serverField = computed<BookField | null>(() => bookFieldOf(this.store.bookError()?.detail ?? null));

  ngOnInit(): void {
    void this.open();
  }

  /** Reads the book and its entries, then puts the book's settings in the form. */
  private async open(): Promise<void> {
    await this.store.load(this.bookId());
    this.fillSettings(this.store.book());
  }

  protected async saveSettings(event: Event): Promise<void> {
    event.preventDefault();
    this.attempted.set(true);
    this.bookSaved.set(false);
    const book = this.store.book();
    const valid =
      (!this.isWorld() || (this.nameValid() && this.descriptionValid())) &&
      this.scanDepthValid() &&
      this.tokenBudgetValid();
    if (book === null || !valid || this.savingBook()) {
      return;
    }
    this.savingBook.set(true);
    try {
      const budget = this.tokenBudget().trim();
      const saved = await this.store.saveBook({
        name: this.isWorld() ? this.name().trim() : book.name,
        description: this.isWorld() ? this.description().trim() : book.description,
        scan_depth: Number(this.scanDepth().trim()),
        token_budget: budget === '' ? null : Number(budget),
      });
      if (saved !== null) {
        this.fillSettings(saved);
        this.attempted.set(false);
        this.bookSaved.set(true);
      }
    } finally {
      this.savingBook.set(false);
    }
  }

  protected async toggleArchive(): Promise<void> {
    await this.store.setArchived(!this.archived());
  }

  protected async confirmRemoveBook(): Promise<void> {
    this.confirmingBookDelete.set(false);
    if (await this.store.removeBook()) {
      this.removed.emit();
    }
  }

  protected newEntry(): void {
    this.store.clearEntryError();
    this.tooLongTitle.set(null);
    this.editing.set('new');
  }

  protected edit(entry: LoreEntry): void {
    this.store.clearEntryError();
    this.tooLongTitle.set(null);
    this.editing.set(entry.id);
  }

  protected closeForm(): void {
    this.editing.set(null);
    this.store.clearEntryError();
  }

  /** The entry the form edits, or null for a new one. */
  protected entryOf(target: string): LoreEntry | null {
    return this.store.entries().find((entry) => entry.id === target) ?? null;
  }

  protected async saveEntry(target: string, fields: EntryFields): Promise<void> {
    if (this.savingEntry()) {
      return;
    }
    this.savingEntry.set(true);
    try {
      const saved = await this.store.saveEntry(target === 'new' ? null : target, fields);
      if (saved !== null) {
        this.editing.set(null);
        this.tooLongTitle.set(saved.never_inserted ? this.titleOf(saved) : null);
      }
    } finally {
      this.savingEntry.set(false);
    }
  }

  protected async confirmRemoveEntry(entry: LoreEntry): Promise<void> {
    this.confirmingDelete.set(null);
    const removed = await this.store.removeEntry(entry);
    if (removed && this.editing() === entry.id) {
      this.editing.set(null);
    }
  }

  protected search(event: Event): void {
    this.store.search((event.target as HTMLInputElement).value);
  }

  /** The title of an entry, or the start of its content when it has none. */
  protected titleOf(entry: LoreEntry): string {
    if (entry.title !== '') {
      return entry.title;
    }
    const start = entry.content.slice(0, 40).trim();
    return entry.content.length > 40 ? `${start}…` : start;
  }

  /** "1 voce" or "N voci". */
  protected countOf(book: LoreBook): string {
    return this.translator.plural('worlds.entries', book.entry_count);
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLTextAreaElement).value;
  }

  private fillSettings(book: LoreBook | null): void {
    this.attempted.set(false);
    this.name.set(book?.name ?? '');
    this.description.set(book?.description ?? '');
    this.scanDepth.set(String(book?.scan_depth ?? LORE_LIMITS.defaultScanDepth));
    this.tokenBudget.set(book?.token_budget === null || book === null ? '' : String(book.token_budget));
  }
}
