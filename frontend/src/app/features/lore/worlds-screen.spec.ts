import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { provideApi } from '../../core/api/providers';
import { LoreBook } from './lore.model';
import { WorldsScreen } from './worlds-screen';

@Component({ selector: 'lyr-stub', template: '' })
class Stub {}

function world(id: string, overrides: Partial<LoreBook> = {}): LoreBook {
  return {
    id,
    kind: 'world',
    character_id: null,
    name: `Mondo ${id}`,
    description: 'Un regno di mare.',
    scan_depth: 4,
    token_budget: null,
    status: 'active',
    entry_count: 2,
    created_at: '2026-11-27T10:00:00.000Z',
    updated_at: '2026-11-27T10:00:00.000Z',
    ...overrides,
  };
}

const isList = (request: { url: string }): boolean => request.url === '/api/v1/lore/worlds';

describe('WorldsScreen', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<WorldsScreen>;
  let root: HTMLElement;

  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      fixture.detectChanges();
    }
  }

  function button(label: string): HTMLButtonElement {
    const found = [...root.querySelectorAll<HTMLButtonElement>('button')].find(
      (item) => (item.textContent ?? '').trim() === label,
    );
    if (found === undefined) {
      throw new Error(`no button says ${label}`);
    }
    return found;
  }

  function type(id: string, value: string): void {
    const control = root.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${id}`);
    if (control === null) {
      throw new Error(`no field ${id}`);
    }
    control.value = value;
    control.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
  }

  const text = (selector: string): string => root.querySelector(selector)?.textContent?.trim() ?? '';

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [provideApi(), provideHttpClientTesting(), provideRouter([{ path: 'worlds/:id', component: Stub }])],
    });
    backend = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(WorldsScreen);
    root = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
    backend.expectOne(isList).flush({ items: [world('a'), world('b', { entry_count: 1, description: '' })] });
    await settle();
  });

  afterEach(() => backend.verify());

  it('lists the worlds with how many entries each holds and a link to its editor', () => {
    expect(root.querySelectorAll('.card')).toHaveLength(2);
    expect(text('h1')).toBe('Mondi');
    const first = root.querySelector('.card');
    expect(first?.textContent).toContain('Mondo a');
    expect(first?.textContent).toContain('2 voci');
    expect(first?.querySelector('a')?.getAttribute('href')).toBe('/worlds/a');
    expect(root.querySelectorAll('.card')[1]?.textContent).toContain('1 voce');
  });

  it('shows the validation error of the name in Italian and creates nothing until it is fixed', async () => {
    button('Nuovo mondo').click();
    fixture.detectChanges();
    root.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    fixture.detectChanges();
    expect(text('#world-name-error')).toBe('Inserisci un nome da 1 a 120 caratteri.');
    backend.expectNone((request) => request.method === 'POST');

    type('world-name', 'Aldara');
    root.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    const post = backend.expectOne('/api/v1/lore/worlds');
    expect(post.request.body).toEqual({ name: 'Aldara', description: '', scan_depth: 4, token_budget: null });
    post.flush(world('n', { name: 'Aldara', entry_count: 0 }));
    await vi.waitFor(() => backend.expectOne(isList).flush({ items: [] }));
    await settle();
    expect(TestBed.inject(Router).url).toBe('/worlds/n');
  });

  it('shows the server refusal of the name next to the field and keeps the form open', async () => {
    button('Nuovo mondo').click();
    fixture.detectChanges();
    type('world-name', 'Il regno delle bambine');
    root.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    backend.expectOne('/api/v1/lore/worlds').flush(
      { code: 'CONTENT_POLICY_BLOCKED', detail: 'Il campo «nome» descrive un minore o un contenuto che l\'app non consente.' },
      { status: 422, statusText: 'Unprocessable Entity' },
    );
    await settle();
    expect(text('#world-name-error')).toContain('«nome»');
    expect(root.querySelector('form')).not.toBeNull();
  });

  it('archives a world and deletes another after a confirmation', async () => {
    button('Archivia').click();
    backend.expectOne('/api/v1/lore/books/a/archive').flush(world('a', { status: 'archived' }));
    await vi.waitFor(() => backend.expectOne(isList).flush({ items: [world('b')] }));
    await settle();
    expect(root.querySelectorAll('.card')).toHaveLength(1);

    button('Elimina').click();
    fixture.detectChanges();
    expect(root.textContent).toContain('Eliminare il mondo «Mondo b» con le sue voci?');
    button('Elimina mondo').click();
    const request = backend.expectOne('/api/v1/lore/books/b');
    expect(request.request.method).toBe('DELETE');
    request.flush(null, { status: 204, statusText: 'No Content' });
    await vi.waitFor(() => backend.expectOne(isList).flush({ items: [] }));
    await settle();
    expect(root.textContent).toContain('Non hai mondi attivi.');
  });

  it('asks for the archived tab', async () => {
    button('Archiviati').click();
    const request = backend.expectOne(isList);
    expect(request.request.params.get('status')).toBe('archived');
    request.flush({ items: [] });
    await settle();
    expect(root.textContent).toContain('Non hai mondi archiviati.');
  });
});
