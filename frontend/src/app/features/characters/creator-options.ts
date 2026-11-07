// The closed choices of the Creator and the catalog text of each, so a template never builds a key by hand.
import { TextKey } from '../../core/i18n/translator';
import { LockableField } from './card.model';

export interface Option {
  readonly value: string;
  readonly label: TextKey;
}

const option = (value: string, label: TextKey): Option => ({ value, label });

export const TRAIT_OPTIONS: readonly Option[] = [
  option('main_trait', 'creator.trait.main_trait'),
  option('value', 'creator.trait.value'),
  option('motivation', 'creator.trait.motivation'),
  option('preference', 'creator.trait.preference'),
  option('flaw', 'creator.trait.flaw'),
  option('fear', 'creator.trait.fear'),
  option('habit', 'creator.trait.habit'),
  option('humor', 'creator.trait.humor'),
  option('temperament', 'creator.trait.temperament'),
  option('relational_style', 'creator.trait.relational_style'),
];

export const FORMALITY_OPTIONS: readonly Option[] = [
  option('molto_informale', 'creator.formality.molto_informale'),
  option('informale', 'creator.formality.informale'),
  option('neutra', 'creator.formality.neutra'),
  option('formale', 'creator.formality.formale'),
];

export const LENGTH_OPTIONS: readonly Option[] = [
  option('brevissimi', 'creator.length.brevissimi'),
  option('brevi', 'creator.length.brevi'),
  option('medi', 'creator.length.medi'),
  option('lunghi', 'creator.length.lunghi'),
];

export const FREQUENCY_OPTIONS: readonly Option[] = [
  option('raramente', 'creator.freq.raramente'),
  option('a_volte', 'creator.freq.a_volte'),
  option('spesso', 'creator.freq.spesso'),
];

export const EXPRESSIVENESS_OPTIONS: readonly Option[] = [
  option('contenuta', 'creator.expressiveness.contenuta'),
  option('media', 'creator.expressiveness.media'),
  option('vivace', 'creator.expressiveness.vivace'),
];

export const ADDRESS_OPTIONS: readonly Option[] = [
  option('tu', 'creator.address.tu'),
  option('lei', 'creator.address.lei'),
];

export const NARRATION_OPTIONS: readonly Option[] = [
  option('nessuna', 'creator.narration.nessuna'),
  option('leggera', 'creator.narration.leggera'),
  option('libera', 'creator.narration.libera'),
];

export const EMOJI_OPTIONS: readonly Option[] = [
  option('mai', 'creator.emoji.mai'),
  option('raramente', 'creator.freq.raramente'),
  option('a_volte', 'creator.freq.a_volte'),
  option('spesso', 'creator.freq.spesso'),
];

export const SUBJECT_OPTIONS: readonly Option[] = [
  option('1girl', 'creator.subject.1girl'),
  option('1boy', 'creator.subject.1boy'),
  option('1other', 'creator.subject.1other'),
];

export const SEED_OPTIONS: readonly Option[] = [
  option('random', 'creator.seed.random'),
  option('fixed', 'creator.seed.fixed'),
  option('base_offset', 'creator.seed.base_offset'),
];

/** The one-line example that shows what each choice does to a reply. */
export const ADDRESS_EXAMPLE: Readonly<Record<string, TextKey>> = {
  tu: 'creator.address.example.tu',
  lei: 'creator.address.example.lei',
};

export const NARRATION_EXAMPLE: Readonly<Record<string, TextKey>> = {
  nessuna: 'creator.narration.example.nessuna',
  leggera: 'creator.narration.example.leggera',
  libera: 'creator.narration.example.libera',
};

export const EMOJI_EXAMPLE: Readonly<Record<string, TextKey>> = {
  mai: 'creator.emoji.example.mai',
  raramente: 'creator.emoji.example.raramente',
  a_volte: 'creator.emoji.example.a_volte',
  spesso: 'creator.emoji.example.spesso',
};

/** The gender that proposes each subject tag while the tag has not been chosen by hand. */
export const SUBJECT_FOR_GENDER: Readonly<Record<string, string>> = {
  femminile: '1girl',
  maschile: '1boy',
  non_indicato: '1other',
};

export const LOCK_LABEL: Readonly<Record<LockableField, TextKey>> = {
  hair: 'creator.lock.hair',
  eyes: 'creator.lock.eyes',
  face: 'creator.lock.face',
  body: 'creator.lock.body',
  distinctive_marks: 'creator.lock.distinctive_marks',
  accessories: 'creator.lock.accessories',
  art_style: 'creator.lock.art_style',
  clothing: 'creator.lock.clothing',
};
