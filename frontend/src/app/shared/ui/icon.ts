// ob-icon: draws one pixel icon of the generated sprite, decorative unless it has a label.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { ICON_SIZES, IconName } from './icon-names';

/** Where the build copies the sprite (angular.json, assets). */
export const SPRITE_URL = '/assets/icons/sprite.svg';

/**
 * Shows the icon `name` in the text color. The size is the side of the icon's grid times `scale`, so the pixels stay
 * square and crisp (a whole number of screen pixels per grid pixel). Without `label` the icon is decorative and hidden
 * from assistive technology; an icon that stands alone must have a `label`.
 */
@Component({
  selector: 'ob-icon',
  template: `
    <svg
      [attr.width]="pixels()"
      [attr.height]="pixels()"
      [attr.role]="label() ? 'img' : null"
      [attr.aria-label]="label() || null"
      [attr.aria-hidden]="label() ? null : 'true'"
      focusable="false"
    >
      <use [attr.href]="href()" />
    </svg>
  `,
  styleUrl: './icon.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObIcon {
  readonly name = input.required<IconName>();
  readonly label = input('');
  /** Whole-number multiplier of the grid size. */
  readonly scale = input(1);

  protected readonly pixels = computed(() => ICON_SIZES[this.name()] * Math.max(1, Math.round(this.scale())));
  protected readonly href = computed(() => `${SPRITE_URL}#${this.name()}`);
}
