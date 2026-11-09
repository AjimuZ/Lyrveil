// Behavior of the control primitives: button, field, menu item and tabs.
import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ObButton } from './button';
import { ObField } from './field';
import { ObMenuItem } from './menu-item';
import { find, findAll, nth, textOf } from './test-dom';
import { ObTabs, TabItem, tabAfterKey } from './tabs';

const root = (fixture: ComponentFixture<unknown>): HTMLElement => fixture.nativeElement as HTMLElement;

@Component({
  selector: 'lyr-button-host',
  imports: [ObButton],
  template: `
    <ob-button [variant]="variant()" [disabled]="disabled()" label="Invia" (click)="count()">Vai</ob-button>
  `,
})
class ButtonHost {
  readonly variant = signal<'primary' | 'secondary' | 'ghost' | 'danger'>('primary');
  readonly disabled = signal(false);
  readonly clicks = signal(0);

  protected count(): void {
    this.clicks.update((total) => total + 1);
  }
}

describe('ob-button', () => {
  function setup(): { fixture: ComponentFixture<ButtonHost>; button: HTMLButtonElement } {
    const fixture = TestBed.createComponent(ButtonHost);
    fixture.detectChanges();
    return { fixture, button: find<HTMLButtonElement>(root(fixture), 'button') };
  }

  it('is a native button with the variant as a class, a type that defaults to button and an accessible name', () => {
    const { fixture, button } = setup();
    expect(button.classList.contains('primary')).toBe(true);
    expect(button.getAttribute('type')).toBe('button');
    expect(button.getAttribute('aria-label')).toBe('Invia');
    expect(textOf(button).trim()).toBe('Vai');

    fixture.componentInstance.variant.set('danger');
    fixture.detectChanges();
    expect(button.classList.contains('danger')).toBe(true);
    expect(button.classList.contains('primary')).toBe(false);
    expect(button.classList.contains('button')).toBe(true);
  });

  it('emits a click when enabled and none when disabled', () => {
    const { fixture, button } = setup();
    button.click();
    expect(fixture.componentInstance.clicks()).toBe(1);

    fixture.componentInstance.disabled.set(true);
    fixture.detectChanges();
    expect(button.disabled).toBe(true);
    button.click();
    expect(fixture.componentInstance.clicks()).toBe(1);
  });
});

@Component({
  selector: 'lyr-field-host',
  imports: [ObField],
  template: `
    <ob-field label="Nome" fid="name" [hint]="hint()" [error]="error()">
      <input id="name" [attr.aria-invalid]="error() ? 'true' : null" />
    </ob-field>
  `,
})
class FieldHost {
  readonly hint = signal('');
  readonly error = signal('');
}

describe('ob-field', () => {
  it('puts the label above the control it points at', () => {
    const fixture = TestBed.createComponent(FieldHost);
    fixture.detectChanges();
    const label = find(root(fixture), 'label');
    expect(textOf(label)).toBe('Nome');
    expect(label.getAttribute('for')).toBe('name');
    expect(find<HTMLInputElement>(root(fixture), 'input').id).toBe('name');
    expect(findAll(root(fixture), '.hint').length).toBe(0);
    expect(findAll(root(fixture), '.error').length).toBe(0);
  });

  it('shows the hint and the error message under stable ids', () => {
    const fixture = TestBed.createComponent(FieldHost);
    fixture.componentInstance.hint.set('Come ti chiami');
    fixture.componentInstance.error.set('Il nome è obbligatorio');
    fixture.detectChanges();
    expect(textOf(find(root(fixture), '#name-hint'))).toBe('Come ti chiami');
    const error = find(root(fixture), '#name-error');
    expect(textOf(error)).toBe('Il nome è obbligatorio');
    expect(error.getAttribute('role')).toBe('alert');
    expect(find(root(fixture), 'input').getAttribute('aria-invalid')).toBe('true');
  });
});

@Component({
  selector: 'lyr-menu-host',
  imports: [ObMenuItem],
  template: `
    <ob-menu-item kind="option" [selected]="selected()">Nerea</ob-menu-item>
    <ob-menu-item [selected]="selected()">Archivia</ob-menu-item>
    <ob-menu-item disabled>Elimina</ob-menu-item>
  `,
})
class MenuHost {
  readonly selected = signal(true);
}

describe('ob-menu-item', () => {
  it('announces the selection as aria-selected for an option and as aria-current for a menu item', () => {
    const fixture = TestBed.createComponent(MenuHost);
    fixture.detectChanges();
    const items = findAll(root(fixture), 'ob-menu-item');
    const option = nth(items, 0);
    const optionButton = find(option, 'button');
    const itemButton = find(nth(items, 1), 'button');
    expect(optionButton.getAttribute('role')).toBe('option');
    expect(optionButton.getAttribute('aria-selected')).toBe('true');
    expect(optionButton.hasAttribute('aria-current')).toBe(false);
    expect(itemButton.getAttribute('role')).toBe('menuitem');
    expect(itemButton.hasAttribute('aria-selected')).toBe(false);
    expect(itemButton.getAttribute('aria-current')).toBe('true');
    expect(option.classList.contains('selected')).toBe(true);

    fixture.componentInstance.selected.set(false);
    fixture.detectChanges();
    expect(optionButton.getAttribute('aria-selected')).toBe('false');
    expect(itemButton.hasAttribute('aria-current')).toBe(false);
    expect(option.classList.contains('selected')).toBe(false);
  });

  it('disables its button', () => {
    const fixture = TestBed.createComponent(MenuHost);
    fixture.detectChanges();
    const items = findAll(root(fixture), 'ob-menu-item');
    expect(find<HTMLButtonElement>(nth(items, 2), 'button').disabled).toBe(true);
    expect(find<HTMLButtonElement>(nth(items, 0), 'button').disabled).toBe(false);
  });
});

const TABS: readonly TabItem[] = [
  { id: 'profile', label: 'Profilo' },
  { id: 'memory', label: 'Ricordi' },
  { id: 'scene', label: 'Scenario' },
];

@Component({
  selector: 'lyr-tabs-host',
  imports: [ObTabs],
  template: `
    <ob-tabs [tabs]="tabs" label="Sezioni" [(selected)]="selected"><p>{{ selected() }}</p></ob-tabs>
  `,
})
class TabsHost {
  readonly tabs = TABS;
  readonly selected = signal('profile');
}

describe('ob-tabs', () => {
  it('moves by arrow, Home and End, wrapping around', () => {
    expect(tabAfterKey('ArrowRight', 0, 3)).toBe(1);
    expect(tabAfterKey('ArrowRight', 2, 3)).toBe(0);
    expect(tabAfterKey('ArrowLeft', 0, 3)).toBe(2);
    expect(tabAfterKey('Home', 2, 3)).toBe(0);
    expect(tabAfterKey('End', 0, 3)).toBe(2);
    expect(tabAfterKey('Enter', 0, 3)).toBeNull();
  });

  function setup(): { fixture: ComponentFixture<TabsHost>; tabs: HTMLButtonElement[] } {
    // TestBed puts the fixture's root element in the document, so focus() works on the tabs.
    const fixture = TestBed.createComponent(TabsHost);
    fixture.detectChanges();
    return { fixture, tabs: findAll<HTMLButtonElement>(root(fixture), '[role="tab"]') };
  }

  function press(tab: HTMLElement, key: string): void {
    tab.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
  }

  it('has one tab in the Tab order, selected, and a panel that it labels', () => {
    const { fixture, tabs } = setup();
    expect(find(root(fixture), '[role="tablist"]').getAttribute('aria-label')).toBe('Sezioni');
    expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
    expect(tabs.map((tab) => tab.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
    const panel = find(root(fixture), '[role="tabpanel"]');
    expect(panel.getAttribute('aria-labelledby')).toBe(nth(tabs, 0).id);
    expect(nth(tabs, 0).getAttribute('aria-controls')).toBe(panel.id);
    expect(textOf(panel)).toBe('profile');
  });

  it('selects a clicked tab', () => {
    const { fixture, tabs } = setup();
    nth(tabs, 1).click();
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toBe('memory');
    expect(nth(tabs, 1).getAttribute('aria-selected')).toBe('true');
    expect(nth(tabs, 1).getAttribute('tabindex')).toBe('0');
    expect(nth(tabs, 0).getAttribute('tabindex')).toBe('-1');
  });

  it('moves the selection and the focus with the keyboard', () => {
    const { fixture, tabs } = setup();
    nth(tabs, 0).focus();
    press(nth(tabs, 0), 'ArrowRight');
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toBe('memory');
    expect(document.activeElement).toBe(nth(tabs, 1));

    press(nth(tabs, 1), 'End');
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toBe('scene');
    expect(document.activeElement).toBe(nth(tabs, 2));

    press(nth(tabs, 2), 'ArrowRight');
    fixture.detectChanges();
    expect(fixture.componentInstance.selected()).toBe('profile');
    expect(document.activeElement).toBe(nth(tabs, 0));
  });

  it('keeps the first tab reachable while the selection names no tab', () => {
    const { fixture, tabs } = setup();
    fixture.componentInstance.selected.set('gone');
    fixture.detectChanges();
    expect(tabs.map((tab) => tab.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
    expect(nth(tabs, 0).getAttribute('aria-selected')).toBe('true');
  });
});
