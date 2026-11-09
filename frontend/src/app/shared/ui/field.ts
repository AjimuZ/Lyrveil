// ob-field: label above a native control, with an optional hint and error message.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/**
 * Lays out the label, the projected control, the hint and the error message. The control is a native `input`,
 * `textarea` or `select` with the id `fid`; the global styles (styles/global.scss) give it the `--ob-bg` background and
 * the `--ob-control-border` border, and `--ob-danger` when it has `aria-invalid="true"`. An invalid control also needs
 * its message: pass it as `error`. The message is in the element `{fid}-error` and the hint in `{fid}-hint`, so the
 * control sets `aria-describedby` to them.
 */
@Component({
  selector: 'ob-field',
  template: `
    <label class="label" [attr.for]="fid()">{{ label() }}</label>
    <ng-content />
    @if (hint()) {
      <p class="hint" [attr.id]="hintId()">{{ hint() }}</p>
    }
    @if (error()) {
      <p class="error" [attr.id]="errorId()" role="alert">{{ error() }}</p>
    }
  `,
  styleUrl: './field.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObField {
  readonly label = input.required<string>();
  /** The id of the projected control. */
  readonly fid = input.required<string>();
  readonly hint = input('');
  /** The message of an invalid control, or empty when the value is fine. */
  readonly error = input('');

  protected readonly hintId = computed(() => `${this.fid()}-hint`);
  protected readonly errorId = computed(() => `${this.fid()}-error`);
}
