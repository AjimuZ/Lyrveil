import { bookFieldOf, entryFieldOf, formatKeys, isOrderNumber, parseKeys } from './lore.model';

const NO_KEYS = { keys: [], secondary_keys: [], exclude_keys: [] } as const;

describe('lore model', () => {
  it('places a refusal of the server on the field its Italian detail names', () => {
    expect(bookFieldOf('Il nome non può essere vuoto.')).toBe('name');
    expect(bookFieldOf('Il campo «messaggi da leggere» deve essere un numero intero tra 1 e 20.')).toBe('scan_depth');
    expect(bookFieldOf('Il campo «budget di token» deve essere un numero intero tra 50 e 900.')).toBe('token_budget');
    expect(bookFieldOf('Il campo «nome» descrive un minore o un contenuto che l\'app non consente.')).toBe('name');
    expect(bookFieldOf('Qualcosa è andato storto.')).toBeNull();
    expect(bookFieldOf(null)).toBeNull();

    expect(entryFieldOf('Il campo «contenuto» supera 8000 caratteri.', NO_KEYS)).toBe('content');
    expect(entryFieldOf('Il campo «priorità» deve essere un numero intero tra -1000000 e 1000000.', NO_KEYS)).toBe(
      'priority',
    );
    // The list names must not be mistaken for each other.
    expect(entryFieldOf('Il campo «parole chiave» può avere al massimo 16 voci.', NO_KEYS)).toBe('keys');
    expect(entryFieldOf('Il campo «parole chiave secondarie» può avere al massimo 16 voci.', NO_KEYS)).toBe(
      'secondary_keys',
    );
    expect(entryFieldOf('Ogni voce di «parole chiave da escludere» può avere al massimo 60 caratteri.', NO_KEYS)).toBe(
      'exclude_keys',
    );
    expect(entryFieldOf('Il campo «sempre attiva» deve essere vero o falso.', NO_KEYS)).toBe('constant');
    expect(entryFieldOf('Il campo «attiva» deve essere vero o falso.', NO_KEYS)).toBe('enabled');
  });

  it('places a key with no useful word on the list that holds it', () => {
    const detail = 'La parola chiave «lui» non ha parole utili: contiene solo parole troppo comuni. Scegline un\'altra.';
    expect(entryFieldOf(detail, { ...NO_KEYS, secondary_keys: ['faro', 'lui'] })).toBe('secondary_keys');
    expect(entryFieldOf(detail, NO_KEYS)).toBeNull();
  });

  it('reads keys like the server stores them and checks the order numbers', () => {
    expect(parseKeys(' faro , scogliera,,faro ,  ')).toEqual(['faro', 'scogliera']);
    expect(formatKeys(['faro', 'scogliera'])).toBe('faro, scogliera');
    expect(['0', '-5', ' 100 ', '1000000'].map(isOrderNumber)).toEqual([true, true, true, true]);
    expect(['', '1.5', 'abc', '1000001', '--2'].map(isOrderNumber)).toEqual([false, false, false, false, false]);
  });
});
