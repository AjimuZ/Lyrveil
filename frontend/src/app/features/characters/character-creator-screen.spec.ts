import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { provideApi } from '../../core/api/providers';
import { CharacterCreatorScreen } from './character-creator-screen';
import { DRAFT_KEY } from './draft-storage';

@Component({ selector: 'lyr-edit-stub', template: '' })
class EditStub {}

const VOCABULARY = {
  items: [
    { tag: 'red hair', category: 'hair_color', aliases_it: ['capelli rossi', 'rosso'] },
    { tag: 'freckles', category: 'face', aliases_it: ['lentiggini'] },
    { tag: 'scar', category: 'face', aliases_it: ['cicatrice'] },
  ],
};

describe('CharacterCreatorScreen', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<CharacterCreatorScreen>;
  let root: HTMLElement;

  /** Waits for the requests and renders until the screen stops changing. */
  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      fixture.detectChanges();
    }
  }

  function input(selector: string): HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement {
    const found = root.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(selector);
    if (found === null) {
      throw new Error(`no control matches ${selector}`);
    }
    return found;
  }

  /** Sets a control as a browser does: `input` then `change`, which is what `selectOption` and typing fire. */
  async function type(selector: string, value: string): Promise<void> {
    const control = input(selector);
    control.value = value;
    control.dispatchEvent(new Event('input', { bubbles: true }));
    control.dispatchEvent(new Event('change', { bubbles: true }));
    control.dispatchEvent(new Event('blur'));
    await settle();
  }

  async function save(): Promise<void> {
    root.querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
    await settle();
  }

  beforeEach(async () => {
    localStorage.removeItem(DRAFT_KEY);
    TestBed.configureTestingModule({
      providers: [
        provideApi(),
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([{ path: 'characters/:id/edit', component: EditStub }]),
      ],
    });
    backend = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(CharacterCreatorScreen);
    root = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
    backend.expectOne('/api/v1/characters/vocabulary').flush(VOCABULARY);
    await settle();
  });

  afterEach(() => {
    localStorage.removeItem(DRAFT_KEY);
    backend.verify();
  });

  it('shows the Italian reason and sends nothing while the age is under 18', async () => {
    await type('#creator-first-name', 'Alba');
    await type('#creator-age', '17');
    await save();

    expect(root.textContent).toContain("L'età deve essere almeno 18.");
    expect(root.textContent).toContain('Alcuni campi non sono validi.');
    backend.expectNone('/api/v1/characters');
  });

  it('creates the character with the card the controls hold', async () => {
    await type('#creator-first-name', 'Nerea');
    await type('#creator-age', '27');
    await type('#creator-gender', 'femminile');
    await type('#creator-hair-color', 'red hair');
    await type('#creator-formality', 'formale');

    // A vocabulary tag is found by its Italian alias and added as a chip.
    await type('#creator-face-tags', 'lentig');
    root.querySelector<HTMLButtonElement>('.suggestion')?.click();
    await settle();
    expect(root.querySelector('.chip')?.textContent).toContain('freckles');

    root.querySelector<HTMLButtonElement>('.tab:nth-child(2)')?.click();
    await settle();
    [...root.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.trim() === 'Aggiungi tratto')?.click();
    await settle();
    await type('textarea[id^="creator-trait-description-"]', 'Ironica e puntuale.');

    await save();
    const request = backend.expectOne((r) => r.method === 'POST' && r.url === '/api/v1/characters');
    const body = request.request.body as Record<string, Record<string, unknown>>;
    expect(body['identity']).toMatchObject({ first_name: 'Nerea', age: 27, grammatical_gender: 'femminile' });
    expect(body['traits']).toEqual([{ category: 'main_trait', description: 'Ironica e puntuale.', locked: false }]);
    // The gender proposed the subject tag and a formal register proposed Lei, because the user chose neither.
    expect(body['visual']).toMatchObject({
      subject_tag: '1girl',
      identity_en: { hair_color: 'red hair', face: ['freckles'] },
    });
    expect(body['communication']).toMatchObject({ formality: 'formale', address_form: 'lei' });
    request.flush({ id: 'c1' });
    await settle();
  });

  it('refuses a reserved outfit key and tells why', async () => {
    root.querySelector<HTMLButtonElement>('.tab:nth-child(4)')?.click();
    await settle();
    [...root.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.trim() === 'Aggiungi abito')?.click();
    await settle();
    await type('#creator-outfit-key-0', 'usual');
    expect(root.textContent).toContain('La chiave di un abito non può essere «usual» né «scene».');
  });

  it('keeps a local draft of a new character until it is saved', async () => {
    await type('#creator-first-name', 'Alba');
    expect(JSON.parse(localStorage.getItem(DRAFT_KEY) ?? '{}')).toMatchObject({
      version: 1,
      model: { identity: { firstName: 'Alba' } },
    });
  });
});
