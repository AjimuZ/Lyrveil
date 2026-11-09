// ob-window: a framed surface; primary windows add stepped corner marks.
import { ChangeDetectionStrategy, Component, booleanAttribute, input } from '@angular/core';

/**
 * Surface with a 1 px frame and `--ob-radius-md`. With `primary` the four corners carry small stepped marks made of
 * `--ob-px` squares in `--ob-accent`; a plain card has none. The marks are decoration and hidden from assistive
 * technology.
 */
@Component({
  selector: 'ob-window',
  template: `
    @if (primary()) {
      <span class="mark top-left" aria-hidden="true"></span>
      <span class="mark top-right" aria-hidden="true"></span>
      <span class="mark bottom-left" aria-hidden="true"></span>
      <span class="mark bottom-right" aria-hidden="true"></span>
    }
    <ng-content />
  `,
  styleUrl: './window.scss',
  host: { '[class.primary]': 'primary()' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObWindow {
  readonly primary = input(false, { transform: booleanAttribute });
}
