import { emptyModel } from './card-form';
import { importedDraft } from './card-import.fixtures';
import {
  CHARS_PER_TOKEN,
  conversionEnded,
  draftToModel,
  estimateTokens,
  fileNameFrom,
  isNativeCharacterFile,
  markOf,
  modelTokenEstimate,
} from './card-import.model';

describe('draftToModel', () => {
  it('leaves the age empty when the card stated none, so the Creator still requires it', () => {
    expect(draftToModel(importedDraft()).identity.age).toBe('');
  });

  it('shows an age the card stated', () => {
    expect(draftToModel(importedDraft({ age: 31 })).identity.age).toBe('31');
  });

  it('keeps the defaults of a new character for what a draft does not have', () => {
    const model = draftToModel(importedDraft());
    expect(model.visual.seedPolicy).toBe(emptyModel().visual.seedPolicy);
    expect(model.visual.hairstyle).toEqual([]);
    expect(model.visual.outfits).toEqual([{ key: 'casa', label: 'Casa', description: '', tags: [] }]);
    expect(model.traits).toEqual([{ category: 'main_trait', description: 'Ironica.', locked: false }]);
    expect(model.communication.examples).toEqual([{ user: '', character: 'Ciao.' }]);
  });

  it('keeps a blank name blank, for the Creator to require', () => {
    expect(draftToModel(importedDraft({ first_name: '' })).identity.firstName).toBe('');
  });
});

describe('markOf', () => {
  it('marks a field the model wrote as proposed, even when the file also had it', () => {
    expect(markOf('identity.biography', ['identity.biography'], ['identity.biography'])).toBe('proposed');
  });

  it('marks a field read from the file and leaves the others unmarked', () => {
    expect(markOf('identity.biography', ['identity.biography'], [])).toBe('card');
    expect(markOf('identity.age', ['identity.biography'], [])).toBeNull();
  });
});

describe('token estimate', () => {
  it('rounds the length over the characters per token up and counts a character once', () => {
    expect(estimateTokens('')).toBe(0);
    expect(estimateTokens('a'.repeat(CHARS_PER_TOKEN))).toBe(1);
    expect(estimateTokens('a'.repeat(CHARS_PER_TOKEN + 1))).toBe(2);
    expect(estimateTokens('😀'.repeat(CHARS_PER_TOKEN))).toBe(1);
  });

  it('counts the texts of the form that reach the prompt', () => {
    const empty = modelTokenEstimate(emptyModel());
    const model = emptyModel();
    model.identity.biography = 'x'.repeat(400);
    expect(modelTokenEstimate(model)).toBeGreaterThanOrEqual(empty + 100);
  });
});

describe('isNativeCharacterFile', () => {
  it('recognizes the native format by its `format` key only', () => {
    expect(isNativeCharacterFile('{"format":"lyrveil.character","format_version":1}')).toBe(true);
    expect(isNativeCharacterFile('{"spec":"chara_card_v2","data":{}}')).toBe(false);
    expect(isNativeCharacterFile('not json')).toBe(false);
    expect(isNativeCharacterFile('[1]')).toBe(false);
  });
});

describe('fileNameFrom', () => {
  it('reads a quoted name and falls back without a header', () => {
    expect(fileNameFrom('attachment; filename="Mara.v2.json"', 'x.json')).toBe('Mara.v2.json');
    expect(fileNameFrom(null, 'x.json')).toBe('x.json');
  });
});

describe('conversionEnded', () => {
  it('is false without a conversion and while one runs', () => {
    expect(conversionEnded(null)).toBe(false);
    expect(
      conversionEnded({ job_id: 'j', status: 'running', error_code: null, error_detail: null, result: null }),
    ).toBe(false);
    expect(
      conversionEnded({ job_id: 'j', status: 'failed', error_code: 'X', error_detail: null, result: null }),
    ).toBe(true);
  });
});
