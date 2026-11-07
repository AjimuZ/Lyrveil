import { Location } from '@angular/common';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { provideApi } from '../../core/api/providers';
import { CharacterProfileScreen } from './character-profile-screen';

@Component({ selector: 'lyr-stub', template: '' })
class Stub {}

const CHARACTER = {
  id: 'c1',
  full_name: 'Nerea Falchi',
  status: 'active',
  is_demo: false,
  current_version_no: 3,
  created_at: '2026-11-07T10:00:00.000Z',
  updated_at: '2026-11-07T10:00:00.000Z',
  card: {
    identity: {
      first_name: 'Nerea',
      last_name: 'Falchi',
      age: 29,
      grammatical_gender: 'femminile',
      narrative_identity: '',
      biography: 'È cresciuta sopra una bottega di bussole.',
      occupation: 'Cartografa',
      interests: ['mappe antiche'],
      background: '',
      setting: '',
      primary_language: 'it',
    },
    traits: [{ category: 'humor', description: 'Ironia asciutta.', locked: true }],
    communication: { examples: [{ user: 'La mappa è pronta?', character: 'ok, era al contrario.' }] },
    visual: {},
  },
};

const VERSIONS = {
  items: [
    { version_no: 3, change_summary: 'Modificati: identità', actor: 'user', created_at: '2026-11-07T12:00:00.000Z' },
    { version_no: 2, change_summary: 'Modificati: tratti', actor: 'user', created_at: '2026-11-07T11:00:00.000Z' },
    { version_no: 1, change_summary: 'Creazione', actor: 'user', created_at: '2026-11-07T10:00:00.000Z' },
  ],
};

describe('CharacterProfileScreen', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<CharacterProfileScreen>;
  let root: HTMLElement;

  /** Lets the pending promises finish (a macrotask flushes the whole chain of an action) and renders. */
  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      await new Promise((resolve) => setTimeout(resolve));
      fixture.detectChanges();
    }
  }

  function button(name: string | RegExp): HTMLButtonElement {
    const found = [...root.querySelectorAll<HTMLButtonElement>('button')].find((candidate) => {
      const label = candidate.getAttribute('aria-label') ?? candidate.textContent?.trim() ?? '';
      return typeof name === 'string' ? label === name : name.test(label);
    });
    if (found === undefined) {
      throw new Error(`no button named ${String(name)}`);
    }
    return found;
  }

  async function click(name: string | RegExp): Promise<void> {
    button(name).click();
    await settle();
  }

  /** Types into the confirmation field of a purge, as a browser does. */
  async function typeName(value: string): Promise<void> {
    const field = root.querySelector<HTMLInputElement>('#purge-name');
    if (field === null) {
      throw new Error('the purge confirmation is not open');
    }
    field.value = value;
    field.dispatchEvent(new Event('input', { bubbles: true }));
    await settle();
  }

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideApi(),
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([
          { path: 'chats', component: Stub },
          { path: 'chats/:conversationId', component: Stub },
          { path: 'characters/:id', component: Stub },
        ]),
      ],
    });
    backend = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(CharacterProfileScreen);
    root = fixture.nativeElement as HTMLElement;
    fixture.componentRef.setInput('id', 'c1');
    fixture.detectChanges();
    backend.expectOne('/api/v1/characters/c1').flush(CHARACTER);
    backend.expectOne('/api/v1/characters/c1/versions').flush(VERSIONS);
    backend.expectOne('/api/v1/conversations?character_id=c1').flush({ items: [] });
    await settle();
    // The avatar tools list the avatars once the character is known; this screen's tests do not look at them.
    backend.expectOne('/api/v1/characters/c1/assets?kind=avatar').flush({ items: [] });
    await settle();
  });

  afterEach(() => {
    backend.verify();
  });

  it('shows the card summary and offers compare and restore on every version but the current one', () => {
    expect(root.textContent).toContain('Nerea Falchi');
    expect(root.textContent).toContain('Ironia asciutta.');
    expect(root.textContent).toContain('ok, era al contrario.');
    expect(root.textContent).toContain('Questo personaggio è simulato');
    expect(root.querySelectorAll('[data-testid="versions"] > li')).toHaveLength(3);
    expect(() => button('Confronta la versione 3 con quella attuale')).toThrow();
    expect(() => button('Ripristina la versione 3')).toThrow();
    expect(button('Confronta la versione 1 con quella attuale')).toBeTruthy();
    expect(button('Ripristina la versione 2')).toBeTruthy();
  });

  it('compares a version with the current one in a table of fields', async () => {
    await click('Confronta la versione 1 con quella attuale');
    backend.expectOne('/api/v1/characters/c1/versions/1/diff/3').flush({
      from_version_no: 1,
      to_version_no: 3,
      changes: [
        { field: 'identity.age', before: 27, after: 29 },
        { field: 'traits', before: [], after: [{ category: 'humor', description: 'Asciutto.', locked: false }] },
      ],
    });
    await settle();

    const rows = [...root.querySelectorAll('.diff tbody tr')].map((row) =>
      [...row.querySelectorAll('th, td')].map((cell) => cell.textContent?.trim()),
    );
    expect(rows[0]).toEqual(['Età', '27', '29']);
    expect(rows[1]?.[0]).toBe('Personalità');
    expect(rows[1]?.[1]).toBe('—');
    expect(root.textContent).toContain('Differenze tra la versione 1 e la versione 3');

    await click('Chiudi il confronto');
    expect(root.querySelector('.diff')).toBeNull();
  });

  it('restores a version, then reads the character and the versions again', async () => {
    await click('Ripristina la versione 1');
    backend.expectOne((r) => r.method === 'POST' && r.url === '/api/v1/characters/c1/versions/1/restore').flush({
      ...CHARACTER,
      current_version_no: 4,
    });
    await settle();
    backend.expectOne('/api/v1/characters/c1').flush({ ...CHARACTER, current_version_no: 4 });
    backend.expectOne('/api/v1/characters/c1/versions').flush({
      items: [
        { version_no: 4, change_summary: 'Ripristinata la versione 1', actor: 'restore', created_at: '2026-11-07T13:00:00.000Z' },
        ...VERSIONS.items,
      ],
    });
    await settle();

    expect(root.textContent).toContain('Versione 1 ripristinata: ora il personaggio è alla versione 4.');
    expect(root.querySelectorAll('[data-testid="versions"] > li')).toHaveLength(4);
  });

  it('archives the character and brings it back', async () => {
    await click('Archivia');
    backend.expectOne((r) => r.method === 'POST' && r.url === '/api/v1/characters/c1/archive').flush({});
    await settle();
    backend.expectOne('/api/v1/characters/c1').flush({ ...CHARACTER, status: 'archived' });
    backend.expectOne('/api/v1/characters/c1/versions').flush(VERSIONS);
    await settle();

    expect(root.textContent).toContain('Personaggio archiviato.');
    expect(root.textContent).toContain('è nascosto dalle liste');
    expect(button('Nuova conversazione').disabled).toBe(true);

    await click("Rimuovi dall'archivio");
    backend.expectOne((r) => r.method === 'POST' && r.url === '/api/v1/characters/c1/unarchive').flush({});
    await settle();
    backend.expectOne('/api/v1/characters/c1').flush(CHARACTER);
    backend.expectOne('/api/v1/characters/c1/versions').flush(VERSIONS);
    await settle();
    expect(button('Archivia')).toBeTruthy();
  });

  it('purges only after the preview and the typed first name', async () => {
    await click('Elimina definitivamente');
    backend.expectOne('/api/v1/characters/c1/purge-preview').flush({ conversations: 1, messages: 12, versions: 3 });
    await settle();

    expect(root.textContent).toContain('Eliminare definitivamente Nerea Falchi?');
    expect(root.textContent).toContain('1 conversazione');
    expect(root.textContent).toContain('12 messaggi');
    expect(root.textContent).toContain('3 versioni della scheda');
    const confirm = button('Elimina per sempre');
    expect(confirm.disabled).toBe(true);

    for (const [typed, enabled] of [
      ['nerea', false],
      ['Nerea Falchi', false],
      ['Nerea', true],
    ] as const) {
      await typeName(typed);
      expect(button('Elimina per sempre').disabled, typed).toBe(!enabled);
    }

    await click('Elimina per sempre');
    const purge = backend.expectOne((r) => r.method === 'POST' && r.url === '/api/v1/characters/c1/purge');
    expect(purge.request.body).toEqual({ confirm_name: 'Nerea' });
    purge.flush(null, { status: 204, statusText: 'No Content' });
    await settle();
    expect(TestBed.inject(Location).path()).toBe('/chats');
  });

  it('shows the explanation of the server and keeps the character when a purge is refused', async () => {
    await click('Elimina definitivamente');
    backend.expectOne('/api/v1/characters/c1/purge-preview').flush({ conversations: 0, messages: 0, versions: 3 });
    await settle();
    await typeName('Nerea');
    await click('Elimina per sempre');
    backend
      .expectOne((r) => r.method === 'POST' && r.url === '/api/v1/characters/c1/purge')
      .flush(
        { code: 'CONFLICT', detail: "Un'operazione del personaggio è ancora in corso. Riprova tra poco." },
        { status: 409, statusText: 'Conflict' },
      );
    await settle();

    expect(root.querySelector('[role="alert"]')?.textContent).toContain("Un'operazione del personaggio è ancora in corso.");
    expect(root.textContent).toContain('Nerea Falchi');
    expect(TestBed.inject(Location).path()).not.toBe('/chats');
  });
});
