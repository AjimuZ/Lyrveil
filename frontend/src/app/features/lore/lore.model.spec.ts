import {
  EntryMatch,
  LoreLink,
  bookFieldOf,
  entryFieldOf,
  formatKeys,
  isOrderNumber,
  matchReasonKey,
  matchReasonParams,
  parseKeys,
  targetOf,
  targetParams,
} from './lore.model';

const NO_KEYS = { keys: [], secondary_keys: [], exclude_keys: [] } as const;

function outcome(overrides: Partial<EntryMatch>): EntryMatch {
  return {
    entry_id: 'e1',
    title: 'Il faro',
    active: false,
    reason: 'no_key_matched',
    matched_key: null,
    matched_secondary_key: null,
    excluded_key: null,
    ...overrides,
  };
}

describe('lore model', () => {
  it('names the one target of a link and builds the body or query that names it', () => {
    const link: LoreLink = {
      id: 'l1',
      book_id: 'w1',
      character_id: null,
      scenario_id: 's1',
      conversation_id: null,
      created_at: '2026-11-27T10:00:00.000Z',
    };
    expect(targetOf(link)).toEqual({ kind: 'scenario', id: 's1' });
    expect(targetOf({ ...link, scenario_id: null, character_id: 'c1' })).toEqual({ kind: 'character', id: 'c1' });
    expect(targetOf({ ...link, scenario_id: null, conversation_id: 'v1' })).toEqual({ kind: 'conversation', id: 'v1' });
    expect(targetOf({ ...link, scenario_id: null })).toBeNull();
    expect(targetParams('conversation', 'v1')).toEqual({ conversation_id: 'v1' });
  });

  it('words the keyword test from the reason, naming the key that decided it', () => {
    const matched = outcome({ active: true, reason: 'matched', matched_key: 'faro' });
    expect(matchReasonKey(matched)).toBe('lore.test.reason.matched');
    expect(matchReasonParams(matched)).toEqual({ key: 'faro', secondary: '' });

    const both = outcome({ active: true, reason: 'matched', matched_key: 'faro', matched_secondary_key: 'notte' });
    expect(matchReasonKey(both)).toBe('lore.test.reason.matchedSecondary');
    expect(matchReasonParams(both)).toEqual({ key: 'faro', secondary: 'notte' });

    // An excluded entry may also have matched a key; the exclude key is the one that decided it.
    const excluded = outcome({ reason: 'excluded', matched_key: 'faro', excluded_key: 'tempesta' });
    expect(matchReasonKey(excluded)).toBe('lore.test.reason.excluded');
    expect(matchReasonParams(excluded).key).toBe('tempesta');

    expect(matchReasonKey(outcome({ reason: 'disabled' }))).toBe('lore.test.reason.disabled');
    expect(matchReasonKey(outcome({ reason: 'constant', active: true }))).toBe('lore.test.reason.constant');
  });

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
