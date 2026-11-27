// Test data for the specs of the card import: a direct draft and an import as the server answers them. Not used by the
// application; the specs import it.
import { CardImport, ImportedDraft } from './card-import.model';

/** A direct draft as the server maps a card that states no age. */
export function importedDraft(overrides: Partial<ImportedDraft['identity']> = {}): ImportedDraft {
  return {
    identity: {
      first_name: 'Mara',
      last_name: null,
      age: null,
      grammatical_gender: 'non_indicato',
      narrative_identity: '',
      biography: 'Una cartografa del porto.',
      occupation: '',
      interests: ['mappe'],
      background: '',
      setting: '',
      primary_language: 'it',
      ...overrides,
    },
    traits: [{ category: 'main_trait', description: 'Ironica.' }],
    communication: {
      formality: 'informale',
      message_length: 'brevi',
      question_frequency: 'a_volte',
      punctuation: '',
      colloquial_style: '',
      expressiveness: 'media',
      initiative_frequency: 'a_volte',
      address_form: 'tu',
      narration: 'nessuna',
      emoji: 'raramente',
      examples: [{ user: null, character: 'Ciao.' }],
      recurring_expressions: [],
      avoid: [],
    },
    visual: {
      face: '',
      hair: '',
      eyes: '',
      body: '',
      clothing: '',
      accessories: '',
      distinctive_marks: '',
      art_style: '',
      subject_tag: '1other',
      outfits: [{ key: 'casa', label: 'Casa', description: '' }],
    },
  };
}

/** An import of a card that states no age, with two greetings, one macro and one text left out. */
export function cardImportFixture(overrides: Partial<CardImport> = {}): CardImport {
  return {
    id: 'imp1',
    status: 'ready',
    format: 'chara_card_v2',
    container: 'png',
    source_sha256: 'abc',
    preview: {
      name: 'Mara',
      nickname: '',
      creator: 'Qualcuno',
      version: '1.2',
      tags: ['porto', 'mappe'],
      creator_notes: 'Una nota dell\'autore.',
      system_prompt: '',
      post_history_instructions: '',
      greeting_count: 2,
      has_avatar: true,
      has_lorebook: false,
    },
    direct_draft: {
      draft: importedDraft(),
      card_fields: ['identity.first_name', 'identity.biography', 'traits'],
      scenarios: [
        { title: 'Mara', description: '', opening_message: 'Ehi.', source: 'data.first_mes' },
        { title: 'Mara (saluto 2)', description: '', opening_message: 'Salve.', source: 'data.alternate_greetings[0]' },
      ],
    },
    report: {
      macros: [{ source: 'data.description', macro: '{{random::a::b}}', replacement: 'a', action: 'first_option', count: 1 }],
      left_out: [
        {
          source: 'data.mes_example',
          target: 'communication.examples',
          reason: 'no_character_line',
          item: 2,
          detail: 'Il blocco 2 degli esempi non ha una riga del personaggio.',
        },
      ],
      age: { status: 'not_stated', value: null, source: null },
    },
    embedded_lyrveil: null,
    conversion_job_id: null,
    conversion: null,
    expires_at: '2026-12-04T10:00:00Z',
    ...overrides,
  };
}
