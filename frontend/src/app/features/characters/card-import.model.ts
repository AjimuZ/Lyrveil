// Shapes of a Character Card import as `POST /characters/imports` answers it (docs/api.md, Character cards), and the pure
// helpers the Creator's import preview needs: a draft becomes a form model, the field marks, and a labelled token estimate.
import { CreatorModel, emptyModel } from './card-form';
import { CharacterCard, ExampleExchange, TraitCategory } from './card.model';

/** The source facts that no draft uses, shown beside the avatar and the name. */
export interface CardPreview {
  readonly name: string;
  readonly nickname: string;
  readonly creator: string;
  readonly version: string;
  readonly tags: readonly string[];
  readonly creator_notes: string;
  readonly system_prompt: string;
  readonly post_history_instructions: string;
  readonly greeting_count: number;
  readonly has_avatar: boolean;
  readonly has_lorebook: boolean;
}

/** A draft of the direct mapping or of the structured conversion: a card without locks, seeds and English tags. */
export interface ImportedDraft {
  readonly identity: {
    readonly first_name: string;
    readonly last_name: string | null;
    /** Null unless the card states an adult age. */
    readonly age: number | null;
    readonly grammatical_gender: CharacterCard['identity']['grammatical_gender'];
    readonly narrative_identity: string;
    readonly biography: string;
    readonly occupation: string;
    readonly interests: readonly string[];
    readonly background: string;
    readonly setting: string;
    readonly primary_language: string;
  };
  readonly traits: readonly { readonly category: TraitCategory; readonly description: string }[];
  readonly communication: CharacterCard['communication'];
  readonly visual: {
    readonly face: string;
    readonly hair: string;
    readonly eyes: string;
    readonly body: string;
    readonly clothing: string;
    readonly accessories: string;
    readonly distinctive_marks: string;
    readonly art_style: string;
    readonly subject_tag: string;
    readonly outfits: readonly { readonly key: string; readonly label: string; readonly description: string }[];
  };
}

/** A scenario that a greeting of the card offers. */
export interface ScenarioProposal {
  readonly title: string;
  readonly description: string;
  readonly opening_message: string;
  readonly source: string;
}

export interface DirectDraft {
  readonly draft: ImportedDraft;
  /** Dotted card paths that the Creator marks **dalla card**. */
  readonly card_fields: readonly string[];
  readonly scenarios: readonly ScenarioProposal[];
}

/** One macro of the card that the import replaced or removed. `action` is placeholder, removed, first_option or reversed. */
export interface MacroUse {
  readonly source: string;
  readonly macro: string;
  readonly replacement: string;
  readonly action: string;
  readonly count: number;
}

/** One card text the mapping did not use; `detail` is Italian and `item` is a `<START>` block number or null. */
export interface LeftOut {
  readonly source: string;
  readonly target: string;
  readonly reason: string;
  readonly item: number | null;
  readonly detail: string;
}

export type AgeStatus = 'stated' | 'not_stated' | 'conflicting' | 'not_adult';

export interface ImportReport {
  readonly macros: readonly MacroUse[];
  readonly left_out: readonly LeftOut[];
  readonly age: { readonly status: AgeStatus; readonly value: number | null; readonly source: string | null };
}

/** The Lyrveil card embedded in the file, already validated by the server. */
export interface EmbeddedLyrveil {
  readonly text_matches: boolean;
  readonly changed_fields: readonly string[];
  readonly card: CharacterCard;
}

export type ConversionStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface Conversion {
  readonly job_id: string;
  readonly status: ConversionStatus;
  readonly error_code: string | null;
  readonly error_detail: string | null;
  readonly result: {
    readonly draft: ImportedDraft;
    readonly proposed_fields: readonly string[];
    readonly attempts: number;
  } | null;
}

/** `POST /characters/imports` and `GET /characters/imports/{id}`. `direct_draft` and `report` are null only for an import stored before P10.2b. */
export interface CardImport {
  readonly id: string;
  readonly status: string;
  readonly format: string;
  readonly container: string;
  readonly source_sha256: string;
  readonly preview: CardPreview;
  readonly direct_draft: DirectDraft | null;
  readonly report: ImportReport | null;
  readonly embedded_lyrveil: EmbeddedLyrveil | null;
  readonly conversion_job_id: string | null;
  readonly conversion: Conversion | null;
  readonly expires_at: string;
}

/** Where the form's content comes from: the embedded Lyrveil card, the direct mapping or the model's conversion. */
export type ImportSource = 'embedded' | 'direct' | 'conversion';

/** How the Creator marks a field: **dalla card** (read from the file) or **proposto** (written by the model). */
export type FieldMark = 'card' | 'proposed';

/** The export formats of `GET /characters/{id}/card`. */
export type CardFormat = 'v3json' | 'v2json' | 'png';

/** Returns whether a conversion has ended, so the screen stops reading it. */
export function conversionEnded(conversion: Conversion | null): boolean {
  return conversion !== null && ['completed', 'failed', 'cancelled'].includes(conversion.status);
}

/**
 * Returns the form model that edits `draft`. The fields a draft lacks keep the defaults of a new character, and a
 * missing age stays empty, so the Creator still requires it.
 */
export function draftToModel(draft: ImportedDraft): CreatorModel {
  const base = emptyModel();
  const { identity, communication, visual } = draft;
  const example = (item: ExampleExchange): { user: string; character: string } => ({
    user: item.user ?? '',
    character: item.character,
  });
  return {
    identity: {
      firstName: identity.first_name,
      lastName: identity.last_name ?? '',
      age: identity.age === null ? '' : String(identity.age),
      gender: identity.grammatical_gender,
      primaryLanguage: identity.primary_language,
      narrativeIdentity: identity.narrative_identity,
      occupation: identity.occupation,
      biography: identity.biography,
      background: identity.background,
      setting: identity.setting,
      interests: identity.interests.map((text) => ({ text })),
    },
    traits: draft.traits.map((trait) => ({ category: trait.category, description: trait.description, locked: false })),
    communication: {
      formality: communication.formality,
      messageLength: communication.message_length,
      questionFrequency: communication.question_frequency,
      punctuation: communication.punctuation,
      colloquialStyle: communication.colloquial_style,
      expressiveness: communication.expressiveness,
      initiativeFrequency: communication.initiative_frequency,
      addressForm: communication.address_form,
      narration: communication.narration,
      emoji: communication.emoji,
      examples: communication.examples.map(example),
      recurringExpressions: communication.recurring_expressions.map((text) => ({ text })),
      avoid: communication.avoid.map((text) => ({ text })),
    },
    visual: {
      ...base.visual,
      face: visual.face,
      hair: visual.hair,
      eyes: visual.eyes,
      body: visual.body,
      clothing: visual.clothing,
      accessories: visual.accessories,
      distinctiveMarks: visual.distinctive_marks,
      artStyle: visual.art_style,
      subjectTag: visual.subject_tag,
      outfits: visual.outfits.map((outfit) => ({ ...outfit, tags: [] })),
    },
  };
}

/** The mark of the card field `path`: **proposto** when the model wrote it, **dalla card** when the file gave it. */
export function markOf(path: string, cardFields: readonly string[], proposed: readonly string[]): FieldMark | null {
  if (proposed.includes(path)) {
    return 'proposed';
  }
  return cardFields.includes(path) ? 'card' : null;
}

/** Characters per token of the estimate; it is the figure of docs/frontend.md for a text runtime that is off. */
export const CHARS_PER_TOKEN = 4;

/** Returns an estimate of the tokens of `text`: its length in characters over `CHARS_PER_TOKEN`, rounded up. */
export function estimateTokens(text: string): number {
  return Math.ceil([...text].length / CHARS_PER_TOKEN);
}

/**
 * Returns the estimated tokens of the texts that reach the character's prompt blocks: the identity texts, the
 * traits and the example exchanges. It only warns that a long card may not fit; it never decides anything.
 */
export function modelTokenEstimate(model: CreatorModel): number {
  const { identity, communication } = model;
  const parts = [
    identity.firstName,
    identity.narrativeIdentity,
    identity.occupation,
    identity.biography,
    identity.background,
    identity.setting,
    ...identity.interests.map((row) => row.text),
    ...model.traits.map((trait) => trait.description),
    ...communication.examples.flatMap((row) => [row.user, row.character]),
    ...communication.recurringExpressions.map((row) => row.text),
    ...communication.avoid.map((row) => row.text),
  ];
  return estimateTokens(parts.join('\n'));
}

/** Returns whether `text` is a `lyrveil.character` file (the native format), which the same import action accepts. */
export function isNativeCharacterFile(text: string): boolean {
  try {
    const parsed: unknown = JSON.parse(text);
    return typeof parsed === 'object' && parsed !== null && (parsed as { format?: unknown }).format === 'lyrveil.character';
  } catch {
    return false;
  }
}

/** Returns the file name from a `Content-Disposition` header, or `fallback` without one. */
export function fileNameFrom(disposition: string | null, fallback: string): string {
  const match = /filename="?([^";]+)"?/i.exec(disposition ?? '');
  return match?.[1] ?? fallback;
}
