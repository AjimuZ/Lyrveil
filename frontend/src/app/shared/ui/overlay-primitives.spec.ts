// Behavior of the overlay primitives: dialog and toast.
import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ObDialog } from './dialog';
import { find, findAll, nth, textOf } from './test-dom';
import { ObToast, TOAST_MS, ToastService } from './toast';

const root = (fixture: ComponentFixture<unknown>): HTMLElement => fixture.nativeElement as HTMLElement;

@Component({
  selector: 'lyr-dialog-host',
  imports: [ObDialog],
  template: `
    <ob-dialog label="Nuova conversazione" [(open)]="open" [dismissable]="dismissable()">
      <button type="button" class="inside">Dentro</button>
    </ob-dialog>
  `,
})
class DialogHost {
  readonly open = signal(false);
  readonly dismissable = signal(true);
}

describe('ob-dialog', () => {
  const original = {
    showModal: HTMLDialogElement.prototype.showModal,
    close: HTMLDialogElement.prototype.close,
  };

  beforeAll(() => {
    // jsdom does not implement modal dialogs: the stubs keep the attribute and the close event of a browser.
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.setAttribute('open', '');
    };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
  });

  afterAll(() => {
    HTMLDialogElement.prototype.showModal = original.showModal;
    HTMLDialogElement.prototype.close = original.close;
  });

  function setup(): { fixture: ComponentFixture<DialogHost>; dialog: HTMLDialogElement } {
    const fixture = TestBed.createComponent(DialogHost);
    fixture.detectChanges();
    return { fixture, dialog: find<HTMLDialogElement>(root(fixture), 'dialog') };
  }

  it('is closed at first, named by its label, and opens and closes with the model', () => {
    const { fixture, dialog } = setup();
    expect(dialog.open).toBe(false);
    expect(dialog.getAttribute('aria-label')).toBe('Nuova conversazione');

    fixture.componentInstance.open.set(true);
    fixture.detectChanges();
    expect(dialog.open).toBe(true);

    fixture.componentInstance.open.set(false);
    fixture.detectChanges();
    expect(dialog.open).toBe(false);
  });

  it('reports a close by the browser (Escape) back to its owner', () => {
    const { fixture, dialog } = setup();
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();

    dialog.removeAttribute('open');
    dialog.dispatchEvent(new Event('close'));
    fixture.detectChanges();
    expect(fixture.componentInstance.open()).toBe(false);
  });

  it('closes on a click on the backdrop but not on its content', () => {
    const { fixture, dialog } = setup();
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();

    find(root(fixture), '.inside').click();
    fixture.detectChanges();
    expect(fixture.componentInstance.open()).toBe(true);

    dialog.click();
    fixture.detectChanges();
    expect(fixture.componentInstance.open()).toBe(false);
    expect(dialog.open).toBe(false);
  });

  it('ignores the backdrop when it is not dismissable', () => {
    const { fixture, dialog } = setup();
    fixture.componentInstance.dismissable.set(false);
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();

    dialog.click();
    fixture.detectChanges();
    expect(fixture.componentInstance.open()).toBe(true);
    expect(dialog.open).toBe(true);
  });
});

describe('ob-toast', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps a toast for four seconds and then removes it', () => {
    vi.useFakeTimers();
    const service = TestBed.inject(ToastService);
    service.show('Salvato', 'success');
    service.show('Qualcosa non va', 'error');
    expect(service.toasts().map((toast) => toast.text)).toEqual(['Salvato', 'Qualcosa non va']);
    expect(TOAST_MS).toBe(4000);

    vi.advanceTimersByTime(TOAST_MS - 1);
    expect(service.toasts().length).toBe(2);
    vi.advanceTimersByTime(1);
    expect(service.toasts().length).toBe(0);
  });

  it('can be dismissed early, one toast at a time', () => {
    vi.useFakeTimers();
    const service = TestBed.inject(ToastService);
    service.show('Uno');
    service.show('Due');
    service.dismiss(nth(service.toasts(), 0).id);
    expect(service.toasts().map((toast) => toast.text)).toEqual(['Due']);
  });

  it('renders the toasts in a polite live region that exists before the first one', () => {
    vi.useFakeTimers();
    const fixture = TestBed.createComponent(ObToast);
    fixture.detectChanges();
    const region = find(root(fixture), '.region');
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(findAll(region, '.toast').length).toBe(0);

    TestBed.inject(ToastService).show('Immagine salvata', 'success');
    fixture.detectChanges();
    expect(findAll(region, '.toast').length).toBe(1);
    expect(textOf(find(region, '.toast'))).toContain('Immagine salvata');
    expect(find(region, '.toast').classList.contains('success')).toBe(true);
  });
});
