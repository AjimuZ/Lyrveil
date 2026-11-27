import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { provideApi } from '../../core/api/providers';
import { CardImport } from './card-import.model';
import { cardImportFixture, importedDraft } from './card-import.fixtures';
import { CharacterCard } from './card.model';
import { CharacterCreatorScreen, DRAFT_POLL_MS } from './character-creator-screen';
import { DRAFT_KEY } from './draft-storage';

@Component({ selector: 'lyr-edit-stub', template: '' })
class EditStub {}

describe('CharacterCreatorScreen with a card import', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<CharacterCreatorScreen>;
  let root: HTMLElement;

  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      fixture.detectChanges();
    }
  }

  async function type(selector: string, value: string): Promise<void> {
    const control = root.querySelector<HTMLInputElement>(selector);
    if (control === null) {
      throw new Error(`no control matches ${selector}`);
    }
    control.value = value;
    control.dispatchEvent(new Event('input', { bubbles: true }));
    control.dispatchEvent(new Event('change', { bubbles: true }));
    control.dispatchEvent(new Event('blur'));
    await settle();
  }

  function button(label: string): HTMLButtonElement {
    const found = [...root.querySelectorAll<HTMLButtonElement>('button')].find((item) =>
      (item.textContent ?? '').includes(label),
    );
    if (found === undefined) {
      throw new Error(`no button says ${label}`);
    }
    return found;
  }

  /** Opens the Creator on `/characters/import/imp1`, with the import the server would answer. */
  async function open(imported: CardImport): Promise<void> {
    fixture = TestBed.createComponent(CharacterCreatorScreen);
    root = fixture.nativeElement as HTMLElement;
    fixture.componentRef.setInput('importId', 'imp1');
    fixture.detectChanges();
    backend.expectOne('/api/v1/characters/vocabulary').flush({ items: [] });
    await settle();
    backend.expectOne('/api/v1/characters/imports/imp1').flush(imported);
    await settle();
  }

  async function save(): Promise<void> {
    root.querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
    await settle();
  }

  beforeEach(() => {
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
  });

  afterEach(() => {
    localStorage.removeItem(DRAFT_KEY);
    backend.verify();
  });

  it('opens with the direct mapping, marks what came from the card and leaves the age empty and required', async () => {
    await open(cardImportFixture());

    expect(root.querySelector('h1')?.textContent).toContain('Nuovo personaggio da una card');
    expect(root.querySelector<HTMLInputElement>('#creator-first-name')?.value).toBe('Mara');
    expect(root.querySelector<HTMLTextAreaElement>('#creator-biography')?.value).toBe('Una cartografa del porto.');
    expect(root.querySelector<HTMLInputElement>('#creator-age')?.value).toBe('');
    const biography = root.querySelector('#creator-biography')?.closest('lyr-field');
    expect(biography?.textContent).toContain('dalla card');
    expect(root.querySelector('#creator-age')?.closest('lyr-field')?.textContent).not.toContain('dalla card');
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull();

    await save();
    expect(root.textContent).toContain('Alcuni campi non sono validi.');
    backend.expectNone((request) => request.method === 'POST');
  });

  it('marks and fills an age that the card stated', async () => {
    const imported = cardImportFixture();
    await open({
      ...imported,
      direct_draft: imported.direct_draft && {
        ...imported.direct_draft,
        draft: importedDraft({ age: 31 }),
        card_fields: [...imported.direct_draft.card_fields, 'identity.age'],
      },
    });

    expect(root.querySelector<HTMLInputElement>('#creator-age')?.value).toBe('31');
    expect(root.querySelector('#creator-age')?.closest('lyr-field')?.textContent).toContain('dalla card');
  });

  it('saves with the import id and the scenarios the user left on', async () => {
    await open(cardImportFixture());
    await type('#creator-age', '33');
    await save();

    const request = backend.expectOne((item) => item.method === 'POST' && item.url === '/api/v1/characters');
    const body = request.request.body as Record<string, unknown>;
    expect(body).toMatchObject({ import_id: 'imp1', create_scenarios: true });
    expect(body['identity']).toMatchObject({ first_name: 'Mara', age: 33 });
    request.flush({ id: 'c1' });
    await settle();
  });

  it('saves without scenarios when the choice is turned off', async () => {
    await open(cardImportFixture());
    await type('#creator-age', '33');
    root.querySelector<HTMLInputElement>('.scenarios input[type="checkbox"]')?.click();
    await settle();
    await save();

    const request = backend.expectOne((item) => item.method === 'POST' && item.url === '/api/v1/characters');
    expect(request.request.body).toMatchObject({ import_id: 'imp1', create_scenarios: false });
    request.flush({ id: 'c1' });
    await settle();
  });

  it('shows the server refusal of a save and keeps the form', async () => {
    await open(cardImportFixture());
    await type('#creator-age', '33');
    await save();
    backend
      .expectOne((item) => item.method === 'POST' && item.url === '/api/v1/characters')
      .flush(
        { code: 'CONFLICT', detail: 'La card è già stata salvata o è scaduta.' },
        { status: 409, statusText: 'Conflict' },
      );
    await settle();

    expect(root.textContent).toContain('La card è già stata salvata o è scaduta.');
    expect(root.querySelector<HTMLInputElement>('#creator-first-name')?.value).toBe('Mara');
  });

  it('starts from the embedded Lyrveil card and switches to the edited texts on request', async () => {
    // The embedded card is a whole Lyrveil card, with an age.
    const card: CharacterCard = {
          identity: {
            first_name: 'Mara Lyr',
            last_name: null,
            age: 29,
            grammatical_gender: 'femminile',
            narrative_identity: '',
            biography: 'Dal personaggio Lyrveil.',
            occupation: '',
            interests: [],
            background: '',
            setting: '',
            primary_language: 'it',
          },
          traits: [],
          communication: importedDraft().communication,
          visual: {
            face: '',
            hair: '',
            eyes: '',
            body: '',
            clothing: '',
            accessories: '',
            distinctive_marks: '',
            art_style: '',
            subject_tag: '1girl',
            identity_en: {
              hair_color: null,
              hair_length: null,
              eye_color: null,
              skin: null,
              hairstyle: [],
              face: [],
              body: [],
              marks: [],
              accessories: [],
              style: [],
              extra: [],
            },
            usual_outfit_en: [],
            outfits: [],
            persistent_negative_en: '',
            locked_fields: [],
            seed_policy: 'random',
            base_seed: null,
          },
    };
    await open(
      cardImportFixture({ embedded_lyrveil: { text_matches: false, changed_fields: ['description'], card } }),
    );

    expect(root.querySelector<HTMLInputElement>('#creator-first-name')?.value).toBe('Mara Lyr');
    expect(root.querySelector<HTMLInputElement>('#creator-age')?.value).toBe('29');

    button('Usa i testi modificati').click();
    await settle();
    expect(root.querySelector<HTMLInputElement>('#creator-first-name')?.value).toBe('Mara');
    expect(root.querySelector<HTMLInputElement>('#creator-age')?.value).toBe('');
  });

  it('structures the card with the model, follows the job, and marks what it proposed', async () => {
    await open(cardImportFixture());
    button('Struttura con il modello').click();
    await settle();
    backend.expectOne('/api/v1/characters/imports/imp1/conversion').flush({
      id: 'j1',
      kind: 'card_conversion',
      status: 'queued',
      queue_position: 1,
    });
    await settle();
    expect(root.textContent).toContain('Il modello sta strutturando la card…');

    await new Promise((resolve) => setTimeout(resolve, DRAFT_POLL_MS + 50));
    const converted = { ...importedDraft({ first_name: 'Mara Dal Modello' }), traits: [] };
    backend.expectOne('/api/v1/characters/imports/imp1').flush(
      cardImportFixture({
        conversion_job_id: 'j1',
        conversion: {
          job_id: 'j1',
          status: 'completed',
          error_code: null,
          error_detail: null,
          result: { draft: converted, proposed_fields: ['identity.biography'], attempts: 1 },
        },
      }),
    );
    await settle();

    expect(root.querySelector<HTMLInputElement>('#creator-first-name')?.value).toBe('Mara Dal Modello');
    expect(root.querySelector('#creator-biography')?.closest('lyr-field')?.textContent).toContain('proposto');
    expect(root.querySelector<HTMLInputElement>('#creator-age')?.value).toBe('');
  });

  it('shows why a conversion failed and keeps the form as it was', async () => {
    await open(cardImportFixture());
    button('Struttura con il modello').click();
    await settle();
    backend
      .expectOne('/api/v1/characters/imports/imp1/conversion')
      .flush({ id: 'j1', kind: 'card_conversion', status: 'queued', queue_position: 1 });
    await new Promise((resolve) => setTimeout(resolve, DRAFT_POLL_MS + 50));
    backend.expectOne('/api/v1/characters/imports/imp1').flush(
      cardImportFixture({
        conversion: {
          job_id: 'j1',
          status: 'failed',
          error_code: 'CARD_TOO_LONG',
          error_detail: 'La card è troppo lunga per il contesto del modello.',
          result: null,
        },
      }),
    );
    await settle();

    expect(root.textContent).toContain('La card è troppo lunga per il contesto del modello.');
    expect(root.querySelector<HTMLInputElement>('#creator-first-name')?.value).toBe('Mara');
  });

  it('shows the load error of an import that expired', async () => {
    fixture = TestBed.createComponent(CharacterCreatorScreen);
    root = fixture.nativeElement as HTMLElement;
    fixture.componentRef.setInput('importId', 'old');
    fixture.detectChanges();
    backend.expectOne('/api/v1/characters/vocabulary').flush({ items: [] });
    await settle();
    backend
      .expectOne('/api/v1/characters/imports/old')
      .flush({ code: 'NOT_FOUND', detail: 'Importazione non trovata.' }, { status: 404, statusText: 'Not Found' });
    await settle();

    expect(root.querySelector('[role="alert"]')).not.toBeNull();
    expect(root.querySelector('form')).toBeNull();
  });
});
