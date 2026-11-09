// Find bar of a conversation (Ctrl+F): a field, the position among the matches, and previous, next and close buttons.
import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, input, output, viewChild } from '@angular/core';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { Translator } from '../../core/i18n/translator';

/**
 * Returns `text` folded for comparison: lower case for `locale` and without accents, so "perche" finds "perché".
 * The same fold is applied to the query and to every message.
 */
export function foldText(text: string, locale: string): string {
  return text.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase(locale);
}

/**
 * The bar is purely presentational: the conversation screen owns the query and the matches. `position` is the 1-based
 * place of the current match, 0 when there is none. Enter goes to the next match and Shift+Enter to the previous
 * one; Esc closes the bar. The count is a polite live region, so every step is read out as "2 di 5".
 */
@Component({
  selector: 'lyr-chat-find',
  imports: [TranslatePipe],
  template: `
    <div class="find" role="search" [attr.aria-label]="'conv.find.label' | t">
      <label class="ob-sr-only" for="chat-find-field">{{ 'conv.find.label' | t }}</label>
      <input
        #field
        id="chat-find-field"
        class="field"
        type="search"
        autocomplete="off"
        [placeholder]="'conv.find.placeholder' | t"
        [value]="query()"
        (input)="queryChange.emit($any($event.target).value)"
        (keydown.enter)="$event.preventDefault(); step.emit(1)"
        (keydown.shift.enter)="$event.preventDefault(); step.emit(-1)"
        (keydown.escape)="$event.stopPropagation(); closed.emit()"
      />
      <span class="count" role="status" data-testid="find-count">{{ countText() }}</span>
      <button type="button" class="button" [attr.aria-label]="'conv.find.previous' | t" [disabled]="count() === 0" (click)="step.emit(-1)">
        <span aria-hidden="true">↑</span>
      </button>
      <button type="button" class="button" [attr.aria-label]="'conv.find.next' | t" [disabled]="count() === 0" (click)="step.emit(1)">
        <span aria-hidden="true">↓</span>
      </button>
      <button type="button" class="button" [attr.aria-label]="'conv.find.close' | t" (click)="closed.emit()">
        <span aria-hidden="true">✕</span>
      </button>
    </div>
  `,
  styleUrl: './chat-find.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatFind {
  private readonly translator = inject(Translator);
  private readonly field = viewChild.required<ElementRef<HTMLInputElement>>('field');

  readonly query = input.required<string>();
  /** How many messages match. */
  readonly count = input.required<number>();
  /** The 1-based place of the current match, or 0. */
  readonly position = input.required<number>();

  readonly queryChange = output<string>();
  readonly step = output<-1 | 1>();
  readonly closed = output();

  protected readonly countText = computed(() => {
    if (this.query().trim() === '') {
      return '';
    }
    return this.count() === 0
      ? this.translator.t('conv.find.none')
      : this.translator.t('conv.find.count', { index: this.position(), total: this.count() });
  });

  /** Puts the caret in the field with its text selected, ready to be replaced. */
  focus(): void {
    const element = this.field().nativeElement;
    element.focus();
    element.select();
  }
}
