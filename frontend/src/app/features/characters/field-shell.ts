// Label, hint and validation message around one Creator control.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { ReadonlyFieldTree } from '@angular/forms/signals';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { FieldMark } from './card-import.model';

/**
 * Wraps a control of the Creator. `fid` is the id of the control inside, which the `<label>` points to. The message of
 * the field's first error shows once the field is touched, so a value is not called wrong before the user left it. A
 * control marked `lock` is placed next to the label.
 */
@Component({
  selector: 'lyr-field',
  imports: [TranslatePipe],
  templateUrl: './field-shell.html',
  styleUrl: './field-shell.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FieldShell {
  readonly label = input.required<string>();
  readonly fid = input.required<string>();
  readonly field = input.required<ReadonlyFieldTree<unknown>>();
  readonly hint = input('');
  /** Where an imported value came from, shown beside the label: **dalla card** or **proposto**. */
  readonly mark = input<FieldMark | null>(null);

  protected readonly message = computed(() => {
    const state = this.field()();
    return state.touched() ? (state.errors()[0]?.message ?? null) : null;
  });
}
