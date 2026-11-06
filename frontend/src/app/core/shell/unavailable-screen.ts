// Placeholder for a routed screen that a later phase delivers: shows the screen's name and says it is not ready.
import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { TranslatePipe } from '../i18n/translate-pipe';
import { TextKey } from '../i18n/translator';

@Component({
  selector: 'lyr-unavailable-screen',
  imports: [TranslatePipe],
  templateUrl: './unavailable-screen.html',
  styleUrl: './unavailable-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class UnavailableScreen {
  /** Catalog key of the screen's title, bound from the route's `data.screen`. */
  readonly screen = input.required<TextKey>();
}
