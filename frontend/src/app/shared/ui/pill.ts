// ob-pill: a status as a small colored square and a word.
import { ChangeDetectionStrategy, Component, input } from '@angular/core';

export type PillTone = 'neutral' | 'success' | 'warning' | 'danger';

/**
 * The projected text is the status word: color alone never carries the status. The 6 px square takes the tone's token
 * color; the text stays `--ob-text`.
 */
@Component({
  selector: 'ob-pill',
  template: `<span class="square" aria-hidden="true"></span><ng-content />`,
  styleUrl: './pill.scss',
  host: { '[class]': 'tone()' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObPill {
  readonly tone = input<PillTone>('neutral');
}
