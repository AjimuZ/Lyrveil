// ob-menu-item: one selectable row of a menu or of the chat list, with the pixel cursor on the selected row.
import { ChangeDetectionStrategy, Component, booleanAttribute, computed, input } from '@angular/core';

/**
 * A button that fills its row. `kind` is `menuitem` in a menu and `option` in a listbox such as the chat list. A
 * selected row has `--ob-selected`, a frame and the stepped cursor; hover and keyboard focus use the same cursor.
 * `selected` is announced with `aria-selected` for an option and with `aria-current` otherwise, because a menu item
 * has no selected state. The click comes from the inner button, so a disabled item never emits it.
 */
@Component({
  selector: 'ob-menu-item',
  template: `
    <button
      type="button"
      class="item"
      [attr.role]="kind()"
      [attr.aria-selected]="ariaSelected()"
      [attr.aria-current]="ariaCurrent()"
      [disabled]="disabled()"
    >
      <span class="cursor" aria-hidden="true"></span>
      <span class="content"><ng-content /></span>
    </button>
  `,
  styleUrl: './menu-item.scss',
  host: { '[class.selected]': 'selected()' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObMenuItem {
  /** The ARIA role of the row. It is not called `role`, which would also leave that attribute on the host element. */
  readonly kind = input<'menuitem' | 'option'>('menuitem');
  readonly selected = input(false, { transform: booleanAttribute });
  readonly disabled = input(false, { transform: booleanAttribute });

  protected readonly ariaSelected = computed(() => (this.kind() === 'option' ? String(this.selected()) : null));
  protected readonly ariaCurrent = computed(() => (this.kind() !== 'option' && this.selected() ? 'true' : null));
}
