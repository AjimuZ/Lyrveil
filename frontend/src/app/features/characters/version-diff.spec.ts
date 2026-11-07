import catalog from '../../../i18n/it.json';
import { fieldLabelKey, formatValue } from './version-diff';

describe('version diff text', () => {
  it('names a card field with a text of the catalog and leaves an unknown path to the screen', () => {
    expect(fieldLabelKey('identity.age')).toBe('creator.age');
    expect(fieldLabelKey('visual.identity_en.hair_color')).toBe('creator.hairColor');
    expect(fieldLabelKey('traits')).toBe('profile.personality');
    expect(fieldLabelKey('visual.future')).toBeNull();
    for (const path of ['identity.first_name', 'communication.avoid', 'visual.locked_fields', 'visual.base_seed']) {
      expect(Object.keys(catalog)).toContain(fieldLabelKey(path));
    }
  });

  it('shows empty values as a dash and lists and objects as readable text', () => {
    expect(formatValue(null)).toBe('—');
    expect(formatValue('')).toBe('—');
    expect(formatValue([])).toBe('—');
    expect(formatValue(28)).toBe('28');
    expect(formatValue(true)).toBe('sì');
    expect(formatValue(['mappe', 'gatti'])).toBe('mappe; gatti');
    expect(formatValue([{ category: 'humor', description: 'Asciutto.', locked: false }])).toBe(
      'category: humor, description: Asciutto., locked: no',
    );
  });
});
