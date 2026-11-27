// `/characters/:id/lore`: the own book of a character, edited with the same entry editor as a world.
import { ChangeDetectionStrategy, Component, OnInit, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { ObSkeleton } from '../../shared/ui/skeleton';
import { BookEditor } from './book-editor';
import { LoreBook } from './lore.model';

/**
 * Shows the book that `GET /characters/{id}/lore` answers, created empty on first use, in the book editor. It has no
 * name, description, archive or delete: the book follows its character. A character that does not exist shows the
 * server's not-found text with a way back; a failed read can be tried again.
 */
@Component({
  selector: 'lyr-character-lore-screen',
  imports: [TranslatePipe, RouterLink, BookEditor, ObSkeleton],
  templateUrl: './character-lore-screen.html',
  styleUrl: './lore-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CharacterLoreScreen implements OnInit {
  private readonly api = inject(ApiClient);

  /** The id of the character, from the route. */
  readonly id = input.required<string>();

  protected readonly book = signal<LoreBook | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<ApiError | null>(null);

  ngOnInit(): void {
    void this.load();
  }

  protected async load(): Promise<void> {
    this.loading.set(true);
    try {
      this.book.set(await firstValueFrom(this.api.get<LoreBook>(`/characters/${this.id()}/lore`)));
      this.error.set(null);
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.loading.set(false);
    }
  }
}
