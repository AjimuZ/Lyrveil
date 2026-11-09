// ob-activity: an indeterminate line that moves in steps while real work runs.
import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * Render it only while the work runs (`@if`): the component animates for as long as it exists. It never shows a
 * percentage. `label` says what the work waits for, as visible text and as the accessible name of the bar.
 */
@Component({
  selector: 'ob-activity',
  template: `
    <div class="line" role="progressbar" [attr.aria-label]="label()"><span class="bar"></span></div>
    <span class="label">{{ label() }}</span>
  `,
  styleUrl: './activity.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObActivity {
  readonly label = input.required<string>();
}
