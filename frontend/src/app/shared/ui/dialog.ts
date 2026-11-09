// ob-dialog: a modal dialog on the native <dialog> element.
import { ChangeDetectionStrategy, Component, ElementRef, effect, input, model, viewChild } from '@angular/core';

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
  host: { '(click)': 'onClick($event)' },
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

  /** A click whose target is the dialog itself landed on its backdrop, since the content fills the box. */
  protected onClick(event: MouseEvent): void {
    if (this.dismissable() && event.target === this.dialog()?.nativeElement) {
      this.open.set(false);
    }
  }
}
