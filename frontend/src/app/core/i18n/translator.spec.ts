import { TestBed } from '@angular/core/testing';

import catalog from '../../../i18n/it.json';
import { Translator } from './translator';

describe('Translator', () => {
  let translator: Translator;

  beforeEach(() => {
    translator = TestBed.inject(Translator);
  });

  it('returns the Italian text of a key', () => {
    expect(translator.t('nav.settings')).toBe('Impostazioni');
  });

  it('selects the plural form with Intl rules and formats the count for it-IT', () => {
    expect(translator.plural('chats.unread', 1)).toBe('1 messaggio non letto');
    expect(translator.plural('chats.unread', 0)).toBe('0 messaggi non letti');
    expect(translator.plural('chats.unread', 1200)).toBe('1200 messaggi non letti');
    expect(translator.plural('chats.unread', 12000)).toBe('12.000 messaggi non letti');
  });

  it('lets params replace the formatted count and ignores params the text does not use', () => {
    expect(translator.plural('chats.unread', 2, { count: 'due' })).toBe('due messaggi non letti');
    expect(translator.t('screen.unavailable', { unused: 'x' })).toBe('Questa schermata non è ancora disponibile.');
  });

  it('has the Italian GPU labels of docs/gpu-orchestration.md for the keys that gpu.state sends', () => {
    // The backend's lyrveil/orchestration/gpu_state.py maps every GPU state to one of these keys, or to none in
    // LLM_READY and GENERATING_TEXT; its tests check that mapping state by state.
    expect(translator.t('gpu.state.waiting')).toBe('In attesa della scheda video…');
    expect(translator.t('gpu.state.drawing')).toBe('Sta disegnando…');
    expect(translator.t('gpu.state.returning')).toBe('Sta tornando in chat…');
    expect(translator.t('gpu.state.recovering')).toBe('Sto riprendendo il controllo della scheda video…');
    expect(translator.t('gpu.state.error')).toBe('La scheda video non risponde. Riprova');
    expect(translator.t('gpu.blocked.insufficientVram')).toBe(
      'Altri programmi stanno usando la scheda video. Chiudili e riprova',
    );
    // Not a GPU state: the queue note of a reply that waits for image work (P06.3).
    expect(translator.t('conv.queuedAfterImage')).toBe("In coda: risponderà appena finisce l'immagine.");
  });

  it('has a non-empty text for every catalog entry and every plural form', () => {
    for (const [key, value] of Object.entries(catalog)) {
      const forms = typeof value === 'string' ? [value] : Object.values(value);
      expect(forms.length, key).toBeGreaterThan(0);
      for (const form of forms) {
        expect(form.trim(), key).not.toBe('');
      }
    }
  });
});
