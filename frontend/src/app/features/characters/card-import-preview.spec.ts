import { ComponentFixture, TestBed } from '@angular/core/testing';

import { CharacterCard } from './card.model';
import { cardImportFixture } from './card-import.fixtures';
import { CardImport, ImportSource } from './card-import.model';
import { CardImportPreview, LONG_CARD_TOKENS } from './card-import-preview';

/** The embedded card is only carried here, never read by the preview. */
const EMBEDDED_CARD = {} as CharacterCard;

describe('CardImportPreview', () => {
  let fixture: ComponentFixture<CardImportPreview>;
  let root: HTMLElement;

  function show(imported: CardImport, source: ImportSource = 'direct', tokens = 120): void {
    fixture.componentRef.setInput('cardImport', imported);
    fixture.componentRef.setInput('source', source);
    fixture.componentRef.setInput('tokens', tokens);
    fixture.componentRef.setInput('createScenarios', true);
    fixture.detectChanges();
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

  beforeEach(() => {
    fixture = TestBed.createComponent(CardImportPreview);
    root = fixture.nativeElement as HTMLElement;
  });

  it('shows what the file held, the report with its source path and an age that is still to write', () => {
    show(cardImportFixture());

    const text = root.textContent ?? '';
    expect(text).toContain('Card importata');
    expect(text).toContain('Qualcuno');
    expect(text).toContain('porto, mappe');
    expect(text).toContain("Una nota dell'autore.");
    expect(text).toContain('Il blocco 2 degli esempi non ha una riga del personaggio.');
    expect(text).toContain('data.mes_example');
    expect(text).toContain('{{random::a::b}}');
    expect(root.querySelector('[data-testid="card-age-report"]')?.textContent).toContain(
      'La card non indica un\'età: scrivila tu, è obbligatoria.',
    );
  });

  it('names the fields marked from the card and those the model wrote', () => {
    show(cardImportFixture());
    fixture.componentRef.setInput('cardFields', ['identity.biography']);
    fixture.componentRef.setInput('proposed', ['identity.occupation']);
    fixture.detectChanges();

    const marks = [...root.querySelectorAll('.marks')].map((item) => item.textContent ?? '');
    expect(marks[0]).toContain('dalla card');
    expect(marks[0]).toContain('Biografia');
    expect(marks[1]).toContain('proposto');
    expect(marks[1]).toContain('Occupazione');
  });

  it('labels the token figure as an estimate and warns when the text is long', () => {
    show(cardImportFixture(), 'direct', 80);
    expect(root.querySelector('[data-testid="card-token-estimate"]')?.textContent).toContain(
      'Stima: circa 80 token',
    );
    expect(root.textContent).not.toContain('potrebbe non entrare');

    show(cardImportFixture(), 'direct', LONG_CARD_TOKENS + 1);
    expect(root.textContent).toContain('potrebbe non entrare');
  });

  it('offers the edited texts when the file was changed outside Lyrveil, and a way back', () => {
    const embedded = {
      text_matches: false,
      changed_fields: ['description', 'mes_example'],
      card: EMBEDDED_CARD,
    };
    const imported = cardImportFixture({ embedded_lyrveil: embedded });
    const chosen: ImportSource[] = [];
    fixture.componentInstance.sourceChange.subscribe((source) => chosen.push(source));

    show(imported, 'embedded');
    expect((root.textContent ?? '').replace(/\s+/g, ' ')).toContain('la descrizione, gli esempi di conversazione.');
    button('Usa i testi modificati').click();
    expect(chosen).toEqual(['direct']);

    show(imported, 'direct');
    button('Torna al personaggio Lyrveil della card').click();
    expect(chosen).toEqual(['direct', 'embedded']);
  });

  it('does not offer the edited texts when the hash matches', () => {
    const embedded = { text_matches: true, changed_fields: [], card: EMBEDDED_CARD };
    show(cardImportFixture({ embedded_lyrveil: embedded }), 'embedded');
    expect(root.textContent).not.toContain('Usa i testi modificati');
  });

  it('asks for the conversion once and disables the button while it runs', () => {
    let asked = 0;
    fixture.componentInstance.convert.subscribe(() => asked++);
    show(cardImportFixture());
    button('Struttura con il modello').click();
    expect(asked).toBe(1);

    fixture.componentRef.setInput('converting', true);
    fixture.detectChanges();
    expect(button('Struttura con il modello').disabled).toBe(true);
    expect(root.textContent).toContain('Il modello sta strutturando la card…');
  });

  it('shows the Italian reason a conversion failed', () => {
    show(cardImportFixture());
    fixture.componentRef.setInput('conversionFailure', 'La card è troppo lunga per il contesto del modello.');
    fixture.detectChanges();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('troppo lunga');
  });

  it('lists the scenarios the greetings offer and reports the choice', () => {
    const choices: boolean[] = [];
    fixture.componentInstance.createScenariosChange.subscribe((value) => choices.push(value));
    show(cardImportFixture());

    expect(root.textContent).toContain('Mara (saluto 2)');
    const box = root.querySelector<HTMLInputElement>('input[type="checkbox"]');
    expect(box?.checked).toBe(true);
    box?.click();
    expect(choices).toEqual([false]);
  });

  it('draws no scenario choice for a card without greetings', () => {
    const imported = cardImportFixture();
    show({ ...imported, direct_draft: imported.direct_draft === null ? null : { ...imported.direct_draft, scenarios: [] } });
    expect(root.querySelector('input[type="checkbox"]')).toBeNull();
  });
});
