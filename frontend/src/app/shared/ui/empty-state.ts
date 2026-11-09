// ob-empty-state: an empty list or screen with a pixel icon, one sentence and one action.
import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { IconName } from './icon-names';
import { ObIcon } from './icon';

/**
 * `icon` is one of the 24 x 24 empty-state icons (`empty-*`), drawn at twice its size so each grid pixel is two screen
 * pixels. `message` is the sentence; the projected content is the action, usually one ob-button.
 */
@Component({
  selector: 'ob-empty-state',
  imports: [ObIcon],
  template: `
    <ob-icon class="icon" [name]="icon()" [scale]="2" />
    <p class="message">{{ message() }}</p>
    <ng-content />
  `,
  styleUrl: './empty-state.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObEmptyState {
  readonly icon = input.required<IconName>();
  readonly message = input.required<string>();
}
