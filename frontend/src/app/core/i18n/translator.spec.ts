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
