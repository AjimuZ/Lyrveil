// **Prova le parole chiave**: paste a text and see which entries of a book would be active, and why.
import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import {
  EntryMatch,
  EntryMatchList,
  MAX_MATCH_TEXT_CHARS,
  matchReasonKey,
  matchReasonParams,
} from './lore.model';

/**
 * The keyword test of the book named by `bookId`. The pasted text is read as one message by the same matcher a reply
 * uses, through `POST /lore/books/{id}/match`; it loads no model and changes nothing, so it works with every model
 * stopped. The answer lists every entry of the book with whether it would be active and the key or the reason behind
 * it. The test reads the book as it is stored, so an entry edited in the editor above is tested after it is saved. A
 * failed request shows the server's reason and keeps the last result.
 */
@Component({
  selector: 'lyr-keyword-test',
  imports: [TranslatePipe],
  templateUrl: './keyword-test.html',
  styleUrl: './keyword-test.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class KeywordTest {
  private readonly api = inject(ApiClient);

  readonly bookId = input.required<string>();

  protected readonly maxChars = MAX_MATCH_TEXT_CHARS;
  protected readonly text = signal('');
  protected readonly attempted = signal(false);
  protected readonly busy = signal(false);
  protected readonly error = signal<ApiError | null>(null);
  /** The last answer, or null before the first test. */
  protected readonly results = signal<readonly EntryMatch[] | null>(null);

  protected readonly textValid = computed(() => {
    const length = this.text().trim().length;
    return length >= 1 && length <= MAX_MATCH_TEXT_CHARS;
  });
  protected readonly activeCount = computed(() => (this.results() ?? []).filter((item) => item.active).length);

  protected async run(event: Event): Promise<void> {
    event.preventDefault();
    this.attempted.set(true);
    if (!this.textValid() || this.busy()) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      const answer = await firstValueFrom(
        this.api.post<EntryMatchList>(`/lore/books/${this.bookId()}/match`, { text: this.text() }),
      );
      this.results.set(answer.items);
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.busy.set(false);
    }
  }

  protected reasonKey(match: EntryMatch): ReturnType<typeof matchReasonKey> {
    return matchReasonKey(match);
  }

  protected reasonParams(match: EntryMatch): ReturnType<typeof matchReasonParams> {
    return matchReasonParams(match);
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLTextAreaElement).value;
  }
}
