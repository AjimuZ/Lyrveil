// ob-dialog: a modal dialog on the native <dialog> element.
import { ChangeDetectionStrategy, Component, ElementRef, effect, input, model, viewChild } from '@angular/core';

/** The elements Tab can reach: links, enabled form controls and anything with a tabindex of 0 or more. */
const TABBABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * `open` is two-way: set it to true to show the dialog and read it to know when the dialog closed by Escape, the close
 * button or a click on the backdrop. `showModal()` makes the rest of the page inert, so Tab stays inside the dialog,
 * and the browser returns focus to the control that opened it when the dialog closes. `label` is the accessible name.
 * Set `dismissable` to false for a dialog that only its own buttons close (it then never closes on a backdrop click).
 */
@Component({
  selector: 'ob-dialog',
  template: `
    <dialog #dialog class="dialog" [attr.aria-label]="label()" (close)="open.set(false)">
      <div class="body"><ng-content /></div>
    </dialog>
  `,
  styleUrl: './dialog.scss',
  host: { '(click)': 'onClick($event)', '(keydown)': 'onKeydown($event)' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObDialog {
  readonly open = model(false);
  readonly label = input.required<string>();
  readonly dismissable = input(true);

  private readonly dialog = viewChild<ElementRef<HTMLDialogElement>>('dialog');

  constructor() {
    effect(() => {
      const element = this.dialog()?.nativeElement;
      if (element === undefined) {
        return;
      }
      if (this.open() && !element.open) {
        element.showModal();
      } else if (!this.open() && element.open) {
        element.close();
      }
    });
  }

  /**
   * Keeps Tab and Shift+Tab inside the dialog: from the last control Tab goes to the first, and from the first (or from
   * the dialog box itself) Shift+Tab goes to the last. The native modal already makes the page inert, but it hands focus
   * to the browser's own interface after the last control, which would leave the dialog.
   */
  protected onKeydown(event: KeyboardEvent): void {
    const box = this.dialog()?.nativeElement;
    if (box === undefined || event.key !== 'Tab' || event.ctrlKey || event.altKey || event.metaKey) {
      return;
    }
    const controls = Array.from(box.querySelectorAll<HTMLElement>(TABBABLE)).filter((control) => control.getClientRects().length > 0);
    const first = controls.at(0);
    const last = controls.at(-1);
    if (first === undefined || last === undefined) {
      event.preventDefault();
      return;
    }
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === box)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  /** A click whose target is the dialog itself landed on its backdrop, since the content fills the box. */
  protected onClick(event: MouseEvent): void {
    if (this.dismissable() && event.target === this.dialog()?.nativeElement) {
      this.open.set(false);
    }
  }
}
