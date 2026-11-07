// Tag list control of the Creator: vocabulary chips with Italian search, or free tags of a few words.
import { ChangeDetectionStrategy, Component, computed, input, model, output, signal } from '@angular/core';
import { FormValueControl } from '@angular/forms/signals';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { sanitizeTag } from './card-rules';
import { SlotCategory, Vocabulary } from './card.model';

/** How many suggestions the vocabulary search lists at most. */
const MAX_SUGGESTIONS = 8;

/**
 * A Signal Forms control whose value is the list of tags. With `category` and `vocabulary` it offers the tags of that
 * category found by English tag or Italian alias, and accepts nothing else. Without them it adds the typed text as a
 * free tag, sanitized as the server sanitizes it. At `limit` tags the search is disabled. Validation messages are shown
 * by the surrounding `lyr-field`, not here.
 */
@Component({
  selector: 'lyr-tag-picker',
  imports: [TranslatePipe],
  templateUrl: './tag-picker.html',
  styleUrl: './tag-picker.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TagPicker implements FormValueControl<string[]> {
  readonly value = model<string[]>([]);
  readonly disabled = input(false);
  readonly touch = output();

  /** The id of the text box, so that the surrounding label points to it. */
  readonly inputId = input.required<string>();
  /** The most tags the list holds. Not `max`: that name is the field's numeric maximum in the control contract. */
  readonly limit = input.required<number>();
  readonly category = input<SlotCategory | null>(null);
  readonly vocabulary = input<Vocabulary | null>(null);

  protected readonly query = signal('');

  protected readonly vocabularyMode = computed(() => this.category() !== null && this.vocabulary() !== null);
  protected readonly full = computed(() => this.value().length >= this.limit());

  /** The entries to offer: not yet chosen, found by the query. Empty until the user types. */
  protected readonly suggestions = computed(() => {
    const vocabulary = this.vocabulary();
    const category = this.category();
    if (vocabulary === null || category === null || this.query().trim() === '') {
      return [];
    }
    const chosen = new Set(this.value());
    return vocabulary
      .search(category, this.query())
      .filter((entry) => !chosen.has(entry.tag))
      .slice(0, MAX_SUGGESTIONS);
  });

  protected readonly noMatch = computed(
    () => this.vocabularyMode() && this.query().trim() !== '' && this.suggestions().length === 0,
  );

  /** The text a chip shows: the tag with its first Italian alias when the vocabulary has one. */
  protected chipText(tag: string): string {
    const vocabulary = this.vocabulary();
    const category = this.category();
    if (vocabulary === null || category === null) {
      return tag;
    }
    const alias = vocabulary.label(category, tag);
    return alias === tag ? tag : `${tag} · ${alias}`;
  }

  protected typed(event: Event): void {
    this.query.set((event.target as HTMLInputElement).value);
  }

  /** Enter adds the first suggestion, or the typed free tag. It never submits the surrounding form. */
  protected enter(event: Event): void {
    event.preventDefault();
    if (this.vocabularyMode()) {
      const first = this.suggestions()[0];
      if (first !== undefined) {
        this.choose(first.tag);
      }
      return;
    }
    this.addFree();
  }

  protected choose(tag: string): void {
    if (this.full() || this.value().includes(tag)) {
      return;
    }
    this.value.update((current) => [...current, tag]);
    this.query.set('');
  }

  protected addFree(): void {
    const tag = sanitizeTag(this.query());
    if (tag === '' || this.full()) {
      return;
    }
    if (!this.value().includes(tag)) {
      this.value.update((current) => [...current, tag]);
    }
    this.query.set('');
  }

  protected remove(tag: string): void {
    this.value.update((current) => current.filter((item) => item !== tag));
    this.touch.emit();
  }
}
