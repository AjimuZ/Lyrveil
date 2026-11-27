// Screen 11 of docs/frontend.md: `/worlds/:id`, the editor of one world.
import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { BookEditor } from './book-editor';

/**
 * Shows `/worlds/:id`: the world's settings and entries in the book editor, with a way back to the list. The `id` of
 * the route is bound to the input. After the world is deleted the screen returns to the list.
 */
@Component({
  selector: 'lyr-world-screen',
  imports: [TranslatePipe, RouterLink, BookEditor],
  templateUrl: './world-screen.html',
  styleUrl: './lore-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WorldScreen {
  private readonly router = inject(Router);

  /** The id of the world, from the route. */
  readonly id = input.required<string>();

  protected leave(): void {
    void this.router.navigate(['/worlds']);
  }
}
