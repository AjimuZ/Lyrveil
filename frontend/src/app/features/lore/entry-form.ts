// The form that creates or edits one lore entry, shared by a world's editor and a character's own book.
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, input, output, signal } from '@angular/core';

import { ApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, Translator } from '../../core/i18n/translator';
import {
  EntryField,
  EntryFields,
  LORE_LIMITS,
  LoreEntry,
  entryFieldOf,
  formatKeys,
  isOrderNumber,
  parseKeys,
} from './lore.model';

type KeyList = 'keys' | 'secondary_keys' | 'exclude_keys';

/**
 * The entry form: **Titolo**, **Contenuto**, **Parole chiave**, **Parole secondarie**, **Parole escluse**, **Sempre
 * attiva**, **Attiva**, **Priorità**, **Ordine**, **Maiuscole esatte** and **Nota**. `entry` is the entry being edited,
 * or null for a new one. The form checks the limits of the server before it emits `saved`, and shows each refusal in
 * Italian next to its field: its own, once a save was tried, and the server's `error`, placed by the field that its
 * `detail` names. A refusal that names no field shows above the form. Saving is the parent's work, so the parent
 * passes `busy` while a request runs. An entry the server flagged `never_inserted` shows a warning, because no reply
 * will ever read it.
 */
@Component({
  selector: 'lyr-entry-form',
  imports: [TranslatePipe],
  templateUrl: './entry-form.html',
  styleUrl: './entry-form.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class EntryForm implements OnInit {
  private readonly translator = inject(Translator);

  readonly entry = input<LoreEntry | null>(null);
  /** The refusal of the last save, or null. */
  readonly error = input<ApiError | null>(null);
  readonly busy = input(false);
  /** The fields to store, emitted only when they pass the limits. */
  readonly saved = output<EntryFields>();
  /** The user gave up the form; nothing was saved. */
  readonly closed = output();

  protected readonly limits = LORE_LIMITS;
  protected readonly attempted = signal(false);

  protected readonly title = signal('');
  protected readonly content = signal('');
  protected readonly keys = signal('');
  protected readonly secondaryKeys = signal('');
  protected readonly excludeKeys = signal('');
  protected readonly constant = signal(false);
  protected readonly enabled = signal(true);
  protected readonly caseSensitive = signal(false);
  protected readonly priority = signal(String(LORE_LIMITS.defaultPriority));
  protected readonly insertionOrder = signal(String(LORE_LIMITS.defaultInsertionOrder));
  protected readonly note = signal('');

  private readonly clientErrors = computed(() => {
    const found: Partial<Record<EntryField, TextKey>> = {};
    if (this.title().trim().length > LORE_LIMITS.title) {
      found.title = 'lore.error.title';
    }
    const length = this.content().trim().length;
    if (length === 0) {
      found.content = 'lore.error.contentEmpty';
    } else if (length > LORE_LIMITS.content) {
      found.content = 'lore.error.contentLong';
    }
    const lists: readonly (readonly [KeyList, string])[] = [
      ['keys', this.keys()],
      ['secondary_keys', this.secondaryKeys()],
      ['exclude_keys', this.excludeKeys()],
    ];
    for (const [field, text] of lists) {
      const parsed = parseKeys(text);
      if (parsed.length > LORE_LIMITS.keysPerList) {
        found[field] = 'lore.error.keysCount';
      } else if (parsed.some((key) => key.length > LORE_LIMITS.keyChars)) {
        found[field] = 'lore.error.keyLength';
      }
    }
    if (!isOrderNumber(this.priority())) {
      found.priority = 'lore.error.number';
    }
    if (!isOrderNumber(this.insertionOrder())) {
      found.insertion_order = 'lore.error.number';
    }
    if (this.note().trim().length > LORE_LIMITS.note) {
      found.note = 'lore.error.note';
    }
    return found;
  });

  /** The field the server's refusal names, or null when it names none. */
  private readonly serverField = computed<EntryField | null>(() =>
    entryFieldOf(this.error()?.detail ?? null, {
      keys: parseKeys(this.keys()),
      secondary_keys: parseKeys(this.secondaryKeys()),
      exclude_keys: parseKeys(this.excludeKeys()),
    }),
  );

  /** The text to show next to each field that has a problem. */
  protected readonly errors = computed(() => {
    const shown: Partial<Record<EntryField, string>> = {};
    if (this.attempted()) {
      for (const [field, key] of Object.entries(this.clientErrors()) as [EntryField, TextKey][]) {
        shown[field] = this.translator.t(key);
      }
    }
    const field = this.serverField();
    const detail = this.error()?.detail ?? null;
    if (field !== null && shown[field] === undefined && detail !== null) {
      shown[field] = detail;
    }
    return shown;
  });

  /** The refusal that names no field, shown above the form. */
  protected readonly generalError = computed<string | null>(() => {
    const failure = this.error();
    if (failure === null || this.serverField() !== null) {
      return null;
    }
    return failure.detail ?? this.translator.t(failure.textKey);
  });

  ngOnInit(): void {
    const entry = this.entry();
    if (entry === null) {
      return;
    }
    this.title.set(entry.title);
    this.content.set(entry.content);
    this.keys.set(formatKeys(entry.keys));
    this.secondaryKeys.set(formatKeys(entry.secondary_keys));
    this.excludeKeys.set(formatKeys(entry.exclude_keys));
    this.constant.set(entry.constant);
    this.enabled.set(entry.enabled);
    this.caseSensitive.set(entry.case_sensitive);
    this.priority.set(String(entry.priority));
    this.insertionOrder.set(String(entry.insertion_order));
    this.note.set(entry.note);
  }

  protected submit(event: Event): void {
    event.preventDefault();
    this.attempted.set(true);
    if (Object.keys(this.clientErrors()).length > 0 || this.busy()) {
      return;
    }
    this.saved.emit({
      title: this.title().trim(),
      content: this.content().trim(),
      keys: parseKeys(this.keys()),
      secondary_keys: parseKeys(this.secondaryKeys()),
      exclude_keys: parseKeys(this.excludeKeys()),
      case_sensitive: this.caseSensitive(),
      constant: this.constant(),
      enabled: this.enabled(),
      priority: Number(this.priority().trim()),
      insertion_order: Number(this.insertionOrder().trim()),
      note: this.note().trim(),
    });
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLTextAreaElement).value;
  }

  protected checked(event: Event): boolean {
    return (event.target as HTMLInputElement).checked;
  }
}
