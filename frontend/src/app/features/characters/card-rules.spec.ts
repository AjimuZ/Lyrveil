import { TestBed } from '@angular/core/testing';

import { Translator } from '../../core/i18n/translator';
import {
  ageProblem,
  exampleProblem,
  freeTagsProblem,
  outfitKeyProblem,
  Problem,
  sanitizeTag,
  seedProblem,
  slotTagProblem,
  slotTagsProblem,
} from './card-rules';
import { Vocabulary } from './card.model';

const vocabulary = new Vocabulary([
  { tag: 'red hair', category: 'hair_color', aliases_it: ['capelli rossi', 'rosso'] },
  { tag: 'freckles', category: 'face', aliases_it: ['lentiggini'] },
]);

/** The Italian text of a refusal, as the form shows it. */
function italian(problem: Problem | null): string | null {
  return problem === null ? null : TestBed.inject(Translator).t(problem.key, problem.params);
}

describe('card rules', () => {
  it('refuses an age under 18 or over 120 and a non-integer, in Italian, as the API does', () => {
    expect(italian(ageProblem('17'))).toBe("L'età deve essere almeno 18.");
    expect(italian(ageProblem('121'))).toBe("L'età deve essere al massimo 120.");
    expect(italian(ageProblem(''))).toBe("L'età deve essere un numero intero.");
    expect(italian(ageProblem('30,5'))).toBe("L'età deve essere un numero intero.");
    expect(ageProblem('18')).toBeNull();
    expect(ageProblem(' 120 ')).toBeNull();
  });

  it('refuses the reserved outfit keys, a malformed key and a repeated key', () => {
    expect(italian(outfitKeyProblem('usual', ['usual']))).toBe('La chiave di un abito non può essere «usual» né «scene».');
    expect(italian(outfitKeyProblem('scene', ['scene']))).toBe('La chiave di un abito non può essere «usual» né «scene».');
    expect(outfitKeyProblem('Sera', ['Sera'])?.key).toBe('creator.error.outfitKeyFormat');
    expect(outfitKeyProblem('1sera', ['1sera'])?.key).toBe('creator.error.outfitKeyFormat');
    expect(outfitKeyProblem('a'.repeat(25), [])?.key).toBe('creator.error.outfitKeyFormat');
    expect(italian(outfitKeyProblem('sera', ['sera', 'sera']))).toBe('Due abiti non possono avere la stessa chiave.');
    expect(outfitKeyProblem('sera', ['sera', 'mare'])).toBeNull();
  });

  it('refuses a slot tag that the vocabulary lacks, and accepts a listed one after sanitizing', () => {
    expect(italian(slotTagProblem(vocabulary, 'hair_color', 'purple hair'))).toBe(
      '«purple hair» non è nel vocabolario dei tag.',
    );
    // A tag listed under another category is unknown for this slot.
    expect(slotTagProblem(vocabulary, 'hair_color', 'freckles')).not.toBeNull();
    expect(slotTagProblem(vocabulary, 'hair_color', '  Red_Hair ')).toBeNull();
    expect(slotTagProblem(vocabulary, 'hair_color', '')).toBeNull();
    expect(slotTagsProblem(vocabulary, 'face', ['freckles', 'scar'])?.key).toBe('creator.error.unknownTag');
    expect(slotTagsProblem(vocabulary, 'face', ['a', 'b', 'c', 'd', 'e'])?.key).toBe('creator.error.tooMany');
  });

  it('limits free tags by count and by four words once sanitized', () => {
    expect(italian(freeTagsProblem(['one two three four five'], 6))).toBe('Ogni tag può avere al massimo 4 parole.');
    expect(freeTagsProblem(['one two three four'], 6)).toBeNull();
    expect(freeTagsProblem(['a', 'b', 'c', 'd', 'e', 'f', 'g'], 6)?.key).toBe('creator.error.tooMany');
  });

  it('sanitizes a tag as the server does', () => {
    expect(sanitizeTag('  Blue_Dress ')).toBe('blue dress');
    expect(sanitizeTag('(red:1.5) ribbon')).toBe('red1.5 ribbon');
    expect(sanitizeTag('embedding:evil hat')).toBe('hat');
    expect(sanitizeTag('a,  b\\c')).toBe('a bc');
    expect(sanitizeTag("o'clock 3.5")).toBe("o'clock 3.5");
  });

  it('needs the answer of the character in an example, and a seed with a seed policy', () => {
    expect(italian(exampleProblem('Ciao', ''))).toBe('Scrivi la risposta del personaggio.');
    expect(exampleProblem('', '')).toBeNull();
    expect(exampleProblem('', 'Ciao!')).toBeNull();
    expect(italian(seedProblem('fixed', ''))).toBe('Con questa politica del seed serve un valore iniziale.');
    expect(seedProblem('random', '')).toBeNull();
    expect(seedProblem('base_offset', '42')).toBeNull();
    expect(seedProblem('fixed', '-1')?.key).toBe('creator.error.seedInvalid');
  });
});
