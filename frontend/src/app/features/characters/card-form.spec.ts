import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { Translator } from '../../core/i18n/translator';
import { creatorSchema, emptyModel, renderIdentity, toCard, toModel, CreatorModel } from './card-form';
import { Vocabulary } from './card.model';

const vocabulary = new Vocabulary([
  { tag: 'red hair', category: 'hair_color', aliases_it: ['capelli rossi', 'rosso'] },
  { tag: 'green eyes', category: 'eye_color', aliases_it: ['occhi verdi'] },
  { tag: 'freckles', category: 'face', aliases_it: ['lentiggini'] },
]);

/** Builds the Creator form on `model` inside an injection context, as the screen does. */
function creator(model = emptyModel()) {
  const state = signal<CreatorModel>(model);
  const tree = TestBed.runInInjectionContext(() =>
    form(state, creatorSchema(() => vocabulary, TestBed.inject(Translator))),
  );
  return { state, tree };
}

describe('creator form validation', () => {
  it('refuses an age under 18 with the Italian message of the API', () => {
    const { tree } = creator();
    tree.identity.age().value.set('17');
    expect(tree.identity.age().errors().map((error) => error.message)).toEqual(["L'età deve essere almeno 18."]);
    tree.identity.age().value.set('18');
    expect(tree.identity.age().errors()).toEqual([]);
  });

  it('requires a first name and refuses one over 80 characters', () => {
    const { tree } = creator();
    expect(tree.identity.firstName().errors()[0]?.message).toBe('Il nome deve avere da 1 a 80 caratteri.');
    tree.identity.firstName().value.set('A'.repeat(81));
    expect(tree.identity.firstName().errors()).toHaveLength(1);
    tree.identity.firstName().value.set('Alba');
    expect(tree.identity.firstName().errors()).toEqual([]);
  });

  it('refuses a reserved outfit key and a repeated one on the outfit that holds it', () => {
    const model = emptyModel();
    model.visual.outfits = [
      { key: 'usual', label: 'Di casa', description: '', tags: [] },
      { key: 'sera', label: 'Sera', description: '', tags: [] },
      { key: 'sera', label: 'Sera due', description: '', tags: [] },
    ];
    const { tree } = creator(model);
    const [reserved, first, second] = [...tree.visual.outfits];
    expect(reserved?.key().errors()[0]?.message).toBe('La chiave di un abito non può essere «usual» né «scene».');
    expect(first?.key().errors()[0]?.message).toBe('Due abiti non possono avere la stessa chiave.');
    expect(second?.key().errors()[0]?.message).toBe('Due abiti non possono avere la stessa chiave.');
    second?.key().value.set('mare');
    expect(first?.key().errors()).toEqual([]);
  });

  it('refuses a vocabulary tag the vocabulary lacks, in a closed slot and in a list', () => {
    const { tree } = creator();
    tree.visual.hairColor().value.set('purple hair');
    tree.visual.faceTags().value.set(['freckles', 'tattoo of a dragon']);
    expect(tree.visual.hairColor().errors()[0]?.message).toBe('«purple hair» non è nel vocabolario dei tag.');
    expect(tree.visual.faceTags().errors()[0]?.message).toBe('«tattoo of a dragon» non è nel vocabolario dei tag.');
    tree.visual.hairColor().value.set('red hair');
    tree.visual.faceTags().value.set(['freckles']);
    expect(tree.visual.hairColor().errors()).toEqual([]);
    expect(tree.visual.faceTags().errors()).toEqual([]);
  });

  it('asks for a starting seed only when the policy uses one', () => {
    const { tree } = creator();
    tree.visual.seedPolicy().value.set('fixed');
    expect(tree.visual.baseSeed().errors()[0]?.message).toBe('Con questa politica del seed serve un valore iniziale.');
    tree.visual.baseSeed().value.set('1234');
    expect(tree.visual.baseSeed().errors()).toEqual([]);
  });

  it('is valid with only a first name and an age', () => {
    const { tree } = creator();
    tree.identity.firstName().value.set('Alba');
    tree.identity.age().value.set('30');
    expect(tree().errorSummary()).toEqual([]);
  });
});

describe('creator model conversion', () => {
  it('sends a trimmed card with blank rows left out and null for an empty optional value', () => {
    const model = emptyModel();
    model.identity.firstName = '  Nerea ';
    model.identity.age = ' 27 ';
    model.identity.interests = [{ text: ' mappe ' }, { text: '  ' }];
    model.traits = [
      { category: 'humor', description: ' Asciutto. ', locked: true },
      { category: 'flaw', description: '   ', locked: false },
    ];
    model.communication.examples = [
      { user: '', character: ' Eccomi. ' },
      { user: '', character: '' },
    ];
    model.visual.hairColor = 'red hair';
    model.visual.seedPolicy = 'fixed';
    model.visual.baseSeed = '42';

    const card = toCard(model) as Record<string, Record<string, unknown>>;
    expect(card['identity']).toMatchObject({ first_name: 'Nerea', last_name: null, age: 27, interests: ['mappe'] });
    expect(card['traits']).toEqual([{ category: 'humor', description: 'Asciutto.', locked: true }]);
    expect(card['communication']?.['examples']).toEqual([{ user: null, character: 'Eccomi.' }]);
    expect(card['visual']).toMatchObject({
      identity_en: { hair_color: 'red hair', eye_color: null },
      seed_policy: 'fixed',
      base_seed: 42,
    });
  });

  it('reads a stored card back into the model it was made from', () => {
    const model = emptyModel();
    model.identity.firstName = 'Nerea';
    model.identity.age = '27';
    model.identity.interests = [{ text: 'mappe' }];
    model.traits = [{ category: 'humor', description: 'Asciutto.', locked: true }];
    model.communication.examples = [{ user: 'Ciao', character: 'Eccomi.' }];
    model.visual.faceTags = ['freckles'];
    model.visual.outfits = [{ key: 'sera', label: 'Sera', description: 'Abito blu', tags: ['blue dress'] }];
    model.visual.lockedFields = ['hair'];
    model.visual.seedPolicy = 'base_offset';
    model.visual.baseSeed = '7';

    const stored = toCard(model) as unknown as Parameters<typeof toModel>[0];
    expect(toModel(stored)).toEqual(model);
  });

  it('renders the English identity in the order of the persistent prompt', () => {
    const model = emptyModel();
    model.visual.eyeColor = 'green eyes';
    model.visual.hairColor = 'red hair';
    model.visual.faceTags = ['freckles'];
    model.visual.extra = ['Brass_Pendant'];
    expect(renderIdentity(model.visual)).toBe('red hair, green eyes, freckles, brass pendant');
  });
});
