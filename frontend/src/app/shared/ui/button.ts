// ob-button: the button of the interface, in four variants.
import { ChangeDetectionStrategy, Component, booleanAttribute, input } from '@angular/core';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

/**
 * Wraps a native button, so keyboard, form submission and `disabled` behave as in HTML. `primary` is the main action
 * (`--ob-primary` with background-color text); `danger` is for destructive actions. A button that shows only an icon
 * must have a `label`, its accessible name. A disabled button is at 55 % opacity and emits no click.
 */
@Component({
  selector: 'ob-button',
  template: `
    <button class="button" [class]="variant()" [attr.type]="type()" [attr.aria-label]="label() || null" [disabled]="disabled()">
      <ng-content />
    </button>
  `,
  styleUrl: './button.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObButton {
  readonly variant = input<ButtonVariant>('secondary');
  readonly type = input<'button' | 'submit' | 'reset'>('button');
  readonly disabled = input(false, { transform: booleanAttribute });
  readonly label = input('');
}
