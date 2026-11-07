// Screen 3 of docs/frontend.md: the Character Creator, a Signal Forms editor of the whole card in four tabs.
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { FormField, form, provideSignalFormsConfig } from '@angular/forms/signals';
import { Router, RouterLink } from '@angular/router';

import { ApiError, toApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, Translator } from '../../core/i18n/translator';
import { AvatarTools } from './avatar-tools';
import { creatorSchema, emptyModel, renderIdentity, toCard, toModel, CreatorModel } from './card-form';
import {
  MAX_AVOIDED,
  MAX_EXAMPLES,
  MAX_EXTRA_TAGS,
  MAX_OUTFIT_TAGS,
  MAX_OUTFITS,
  MAX_RECURRING_EXPRESSIONS,
  MAX_SLOT_TAGS,
} from './card-rules';
import { DraftResult, LOCKABLE_FIELDS, LockableField, QueuedJob, Vocabulary } from './card.model';
import { CharactersStore } from './characters-store';
import {
  ADDRESS_EXAMPLE,
  ADDRESS_OPTIONS,
  EMOJI_EXAMPLE,
  EMOJI_OPTIONS,
  EXPRESSIVENESS_OPTIONS,
  FORMALITY_OPTIONS,
  FREQUENCY_OPTIONS,
  LENGTH_OPTIONS,
  LOCK_LABEL,
  NARRATION_EXAMPLE,
  NARRATION_OPTIONS,
  SEED_OPTIONS,
  SUBJECT_FOR_GENDER,
  SUBJECT_OPTIONS,
  TRAIT_OPTIONS,
} from './creator-options';
import { clearDraft, readDraft, writeDraft } from './draft-storage';
import { FieldShell } from './field-shell';
import { HasUnsavedChanges } from './leave-guard';
import { TagPicker } from './tag-picker';
import { fieldLabelKey } from './version-diff';
import { GENDER_OPTIONS, LANGUAGE_OPTIONS } from '../chats/new-chat-dialog';

export type CreatorTab = 'identity' | 'personality' | 'communication' | 'appearance';

type Phase = 'loading' | 'ready' | 'failed';
/** The assisted draft: nothing asked, a job queued or running, a draft shown in the form, or a refusal. */
type AssistState = 'idle' | 'waiting' | 'ready' | 'failed';

/** How often the screen reads the draft job while it waits. */
export const DRAFT_POLL_MS = 1000;
/** The longest description the server accepts (`POST /characters/drafts`). */
export const MAX_DESCRIPTION_LENGTH = 2000;

/**
 * Creates a character (`/characters/new`) or edits one (`/characters/:id/edit`). The form model is a signal that
 * Signal Forms wraps; the schema of `card-form` validates it with Italian messages. Nothing is sent while a field is
 * invalid. A new character keeps a local draft in the browser until it is saved, and leaving with unsaved changes asks
 * first (`leaveWithConfirmation`). A refusal from the server stays on the screen with its explanation.
 *
 * The assist panel asks the server for a draft from a description (`POST /characters/drafts`) and reads its job every
 * DRAFT_POLL_MS until it ends. A completed draft fills the form but is not the saved state: the form stays unsaved, the
 * fields the model filled are listed, and only **Salva personaggio** stores it. Over an existing character the server
 * keeps its locks.
 *
 * The Images tab holds the avatar tools of an existing character; a new one gets them after its first save, because
 * assets belong to a stored character. Not delivered here, so no control is drawn for it: import and export, the token counter of the
 * examples and the first-chunk warning of the persistent prompt.
 */
@Component({
  selector: 'lyr-character-creator',
  imports: [FormField, FieldShell, TagPicker, TranslatePipe, RouterLink, AvatarTools],
  templateUrl: './character-creator-screen.html',
  styleUrl: './character-creator-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  providers: [
    // A touched, invalid control gets the class that draws its red border; the message comes from `lyr-field`.
    provideSignalFormsConfig({
      classes: { 'is-invalid': (binding) => binding.state().touched() && binding.state().invalid() },
    }),
  ],
})
export class CharacterCreatorScreen implements OnInit, HasUnsavedChanges {
  private readonly store = inject(CharactersStore);
  private readonly router = inject(Router);
  private readonly translator = inject(Translator);
  private readonly destroyRef = inject(DestroyRef);

  /** The character to edit, from the route; absent on `/characters/new`. */
  readonly id = input<string>();

  protected readonly tabs: readonly { id: CreatorTab; label: TextKey }[] = [
    { id: 'identity', label: 'creator.tab.identity' },
    { id: 'personality', label: 'creator.tab.personality' },
    { id: 'communication', label: 'creator.tab.communication' },
    { id: 'appearance', label: 'creator.tab.appearance' },
  ];
  protected readonly genders = GENDER_OPTIONS;
  protected readonly languages = LANGUAGE_OPTIONS;
  protected readonly traitOptions = TRAIT_OPTIONS;
  protected readonly formalities = FORMALITY_OPTIONS;
  protected readonly lengths = LENGTH_OPTIONS;
  protected readonly frequencies = FREQUENCY_OPTIONS;
  protected readonly expressiveness = EXPRESSIVENESS_OPTIONS;
  protected readonly addresses = ADDRESS_OPTIONS;
  protected readonly narrations = NARRATION_OPTIONS;
  protected readonly emojis = EMOJI_OPTIONS;
  protected readonly subjects = SUBJECT_OPTIONS;
  protected readonly seedPolicies = SEED_OPTIONS;
  protected readonly lockable = LOCKABLE_FIELDS;
  protected readonly lockLabel = LOCK_LABEL;
  protected readonly limits = {
    slotTags: MAX_SLOT_TAGS,
    extraTags: MAX_EXTRA_TAGS,
    outfitTags: MAX_OUTFIT_TAGS,
    outfits: MAX_OUTFITS,
    examples: MAX_EXAMPLES,
    expressions: MAX_RECURRING_EXPRESSIONS,
    avoided: MAX_AVOIDED,
  } as const;

  protected readonly phase = signal<Phase>('loading');
  protected readonly loadError = signal<ApiError | null>(null);
  protected readonly saveError = signal<ApiError | null>(null);
  protected readonly busy = signal(false);
  protected readonly tab = signal<CreatorTab>('identity');
  protected readonly attempted = signal(false);
  protected readonly notice = signal<'created' | 'saved' | 'unchanged' | 'draft' | null>(null);
  protected readonly versionNo = signal<number | null>(null);
  protected readonly fullName = signal('');

  protected readonly assistLimit = MAX_DESCRIPTION_LENGTH;
  protected readonly assistText = signal('');
  protected readonly assistState = signal<AssistState>('idle');
  /** The draft job as last read, for its queue position and status while the screen waits. */
  protected readonly assistJob = signal<QueuedJob | null>(null);
  /** The Italian reason a draft was refused or failed. */
  protected readonly assistError = signal('');
  /** The card fields the last draft filled in, as dotted paths. */
  protected readonly proposed = signal<readonly string[]>([]);
  /** The labels of the proposed fields, once each, in card order; a path without a label is left out. */
  protected readonly proposedLabels = computed(() => {
    const keys = this.proposed()
      .map((path) => fieldLabelKey(path))
      .filter((key) => key !== null);
    return [...new Set(keys)].map((key) => this.translator.t(key)).join(', ');
  });
  private pollTimer: ReturnType<typeof setTimeout> | undefined;
  private destroyed = false;

  protected readonly vocabulary = signal(new Vocabulary([]));
  protected readonly model = signal<CreatorModel>(emptyModel());
  protected readonly creatorForm = form(this.model, creatorSchema(() => this.vocabulary(), this.translator), {
    name: 'character',
  });

  private readonly baseline = signal(JSON.stringify(emptyModel()));
  /** True while the form differs from what was loaded or last saved. */
  protected readonly dirty = computed(() => JSON.stringify(this.model()) !== this.baseline());
  protected readonly persistentIdentity = computed(() => renderIdentity(this.model().visual));
  protected readonly addressExample = computed(
    () => ADDRESS_EXAMPLE[this.model().communication.addressForm] ?? 'creator.address.example.tu',
  );
  protected readonly narrationExample = computed(
    () => NARRATION_EXAMPLE[this.model().communication.narration] ?? 'creator.narration.example.nessuna',
  );
  protected readonly emojiExample = computed(
    () => EMOJI_EXAMPLE[this.model().communication.emoji] ?? 'creator.emoji.example.raramente',
  );

  /** The invalid tabs, known once a save was tried or a field of the tab was touched. */
  protected readonly invalidTabs = computed<readonly CreatorTab[]>(() => {
    const f = this.creatorForm;
    const flagged: [CreatorTab, boolean][] = [
      ['identity', f.identity().errorSummary().length > 0],
      ['personality', f.traits().errorSummary().length > 0],
      ['communication', f.communication().errorSummary().length > 0],
      ['appearance', f.visual().errorSummary().length > 0],
    ];
    return flagged.filter(([, invalid]) => invalid).map(([name]) => name);
  });

  // Whether the subject tag and the address form still follow the gender and the register, as long as the user has
  // not chosen them. A stored character starts without the proposals.
  private followGender = true;
  private followRegister = true;

  constructor() {
    // External sync: the draft in the browser follows the form while a new character is being written.
    effect(() => {
      const model = this.model();
      if (this.phase() === 'ready' && this.id() === undefined) {
        untracked(() => (this.dirty() ? writeDraft(model) : clearDraft()));
      }
    });
    this.destroyRef.onDestroy(() => {
      this.destroyed = true;
      clearTimeout(this.pollTimer);
    });
  }

  async ngOnInit(): Promise<void> {
    try {
      this.vocabulary.set(await this.store.vocabulary());
      const id = this.id();
      if (id === undefined) {
        const draft = readDraft();
        if (draft !== null) {
          this.model.set(draft);
          this.notice.set('draft');
        }
      } else {
        const stored = await this.store.load(id);
        this.adopt(toModel(stored.card));
        this.versionNo.set(stored.current_version_no);
        this.fullName.set(stored.full_name);
        this.followGender = stored.card.visual.subject_tag === '1other';
        this.followRegister = false;
        this.notice.set(this.store.takeCreated(id) ? 'created' : null);
      }
      this.phase.set('ready');
    } catch (failure) {
      this.loadError.set(toApiError(failure));
      this.phase.set('failed');
    }
  }

  hasUnsavedChanges(): boolean {
    return this.dirty();
  }

  protected show(tab: CreatorTab): void {
    this.tab.set(tab);
  }

  protected isTabInvalid(tab: CreatorTab): boolean {
    return this.invalidTabs().includes(tab);
  }

  // Row lists. Each change replaces the array, so the form sees a new value.

  protected addTrait(): void {
    this.model.update((m) => ({
      ...m,
      traits: [...m.traits, { category: 'main_trait', description: '', locked: false }],
    }));
  }

  protected removeTrait(index: number): void {
    this.model.update((m) => ({ ...m, traits: m.traits.filter((_, at) => at !== index) }));
  }

  protected toggleTraitLock(index: number): void {
    this.model.update((m) => ({
      ...m,
      traits: m.traits.map((trait, at) => (at === index ? { ...trait, locked: !trait.locked } : trait)),
    }));
  }

  protected addInterest(): void {
    this.model.update((m) => ({ ...m, identity: { ...m.identity, interests: [...m.identity.interests, { text: '' }] } }));
  }

  protected removeInterest(index: number): void {
    this.model.update((m) => ({
      ...m,
      identity: { ...m.identity, interests: m.identity.interests.filter((_, at) => at !== index) },
    }));
  }

  protected addExample(): void {
    this.model.update((m) => ({
      ...m,
      communication: { ...m.communication, examples: [...m.communication.examples, { user: '', character: '' }] },
    }));
  }

  protected removeExample(index: number): void {
    this.model.update((m) => ({
      ...m,
      communication: { ...m.communication, examples: m.communication.examples.filter((_, at) => at !== index) },
    }));
  }

  /** Moves an example by `step` places (-1 up, 1 down); the saved order is the order in the prompt. */
  protected moveExample(index: number, step: -1 | 1): void {
    this.model.update((m) => {
      const examples = [...m.communication.examples];
      const moved = examples[index];
      const other = examples[index + step];
      if (moved === undefined || other === undefined) {
        return m;
      }
      examples[index] = other;
      examples[index + step] = moved;
      return { ...m, communication: { ...m.communication, examples } };
    });
  }

  protected addExpression(): void {
    this.model.update((m) => ({
      ...m,
      communication: {
        ...m.communication,
        recurringExpressions: [...m.communication.recurringExpressions, { text: '' }],
      },
    }));
  }

  protected removeExpression(index: number): void {
    this.model.update((m) => ({
      ...m,
      communication: {
        ...m.communication,
        recurringExpressions: m.communication.recurringExpressions.filter((_, at) => at !== index),
      },
    }));
  }

  protected addAvoided(): void {
    this.model.update((m) => ({
      ...m,
      communication: { ...m.communication, avoid: [...m.communication.avoid, { text: '' }] },
    }));
  }

  protected removeAvoided(index: number): void {
    this.model.update((m) => ({
      ...m,
      communication: { ...m.communication, avoid: m.communication.avoid.filter((_, at) => at !== index) },
    }));
  }

  protected addOutfit(): void {
    this.model.update((m) => ({
      ...m,
      visual: {
        ...m.visual,
        outfits: [...m.visual.outfits, { key: '', label: '', description: '', tags: [] }],
      },
    }));
  }

  protected removeOutfit(index: number): void {
    this.model.update((m) => ({ ...m, visual: { ...m.visual, outfits: m.visual.outfits.filter((_, at) => at !== index) } }));
  }

  protected isLocked(field: LockableField): boolean {
    return this.model().visual.lockedFields.includes(field);
  }

  protected toggleLock(field: LockableField): void {
    this.model.update((m) => ({
      ...m,
      visual: {
        ...m.visual,
        lockedFields: m.visual.lockedFields.includes(field)
          ? m.visual.lockedFields.filter((item) => item !== field)
          : [...m.visual.lockedFields, field],
      },
    }));
  }

  /** The user chose how the character speaks of itself: the subject tag follows, unless it was chosen by hand. */
  protected genderChosen(event: Event): void {
    // The value is read from the control, because the form field may not have taken it yet when this handler runs.
    const proposed = SUBJECT_FOR_GENDER[(event.target as HTMLSelectElement).value];
    if (this.followGender && proposed !== undefined) {
      this.creatorForm.visual.subjectTag().value.set(proposed);
    }
  }

  /** The user chose the register: a formal one proposes *Lei*, unless the form of address was chosen by hand. */
  protected registerChosen(event: Event): void {
    if (this.followRegister && (event.target as HTMLSelectElement).value === 'formale') {
      this.creatorForm.communication.addressForm().value.set('lei');
    }
  }

  /** The user chose the subject tag, so the gender stops proposing one. */
  protected subjectChosen(): void {
    this.followGender = false;
  }

  /** The user chose the form of address, so a formal register stops proposing *Lei*. */
  protected addressChosen(): void {
    this.followRegister = false;
  }

  protected assistInput(event: Event): void {
    this.assistText.set((event.target as HTMLTextAreaElement).value);
  }

  /** Asks for a draft of the description; the screen then follows its job until it ends. */
  protected async requestDraft(): Promise<void> {
    const description = this.assistText().trim();
    if (description === '' || this.assistState() === 'waiting') {
      return;
    }
    this.assistState.set('waiting');
    this.assistError.set('');
    this.proposed.set([]);
    try {
      const job = await this.store.startDraft(description, this.id() ?? null);
      this.assistJob.set(job);
      this.followDraft(job.id);
    } catch (failure) {
      const error = toApiError(failure);
      this.failDraft(error.detail ?? this.translator.t(error.textKey));
    }
  }

  private followDraft(jobId: string): void {
    this.pollTimer = setTimeout(() => void this.readDraft(jobId), DRAFT_POLL_MS);
  }

  private async readDraft(jobId: string): Promise<void> {
    if (this.destroyed) {
      return;
    }
    try {
      const job = await this.store.draftJob(jobId);
      if (this.destroyed) {
        return;
      }
      if (job.status === 'completed' && job.result !== null) {
        this.showDraft(job.result);
      } else if (job.status === 'failed' || job.status === 'cancelled') {
        this.failDraft(job.error_detail ?? this.translator.t('creator.assist.failed'));
      } else {
        this.assistJob.set(job);
        this.followDraft(jobId);
      }
    } catch (failure) {
      const error = toApiError(failure);
      this.failDraft(error.detail ?? this.translator.t(error.textKey));
    }
  }

  /** Fills the form with the draft without taking it as the saved state, so it stays unsaved until the user saves. */
  private showDraft(result: DraftResult): void {
    this.model.set(toModel(result.card));
    this.followGender = false;
    this.followRegister = false;
    this.proposed.set(result.proposed_fields);
    this.assistState.set('ready');
    this.notice.set(null);
  }

  private failDraft(reason: string): void {
    this.assistError.set(reason);
    this.assistState.set('failed');
  }

  /** Drops the restored draft and starts again from an empty form. */
  protected discardDraft(): void {
    clearDraft();
    this.followGender = true;
    this.followRegister = true;
    this.adopt(emptyModel());
    this.notice.set(null);
  }

  protected async save(event: Event): Promise<void> {
    event.preventDefault();
    if (this.busy()) {
      return;
    }
    this.attempted.set(true);
    this.saveError.set(null);
    this.creatorForm().markAsTouched();
    const problem = this.creatorForm().errorSummary()[0];
    if (problem !== undefined) {
      const first = this.invalidTabs()[0];
      if (first !== undefined) {
        this.tab.set(first);
      }
      setTimeout(() => problem.fieldTree().focusBoundControl(), 0);
      return;
    }
    this.busy.set(true);
    try {
      const body = toCard(this.model());
      const id = this.id();
      if (id === undefined) {
        const created = await this.store.create(body);
        this.baseline.set(JSON.stringify(this.model()));
        clearDraft();
        await this.router.navigate(['/characters', created.id, 'edit']);
      } else {
        const stored = await this.store.update(id, body);
        this.notice.set(stored.current_version_no === this.versionNo() ? 'unchanged' : 'saved');
        this.versionNo.set(stored.current_version_no);
        this.fullName.set(stored.full_name);
        this.adopt(toModel(stored.card));
      }
    } catch (failure) {
      this.saveError.set(toApiError(failure));
    } finally {
      this.busy.set(false);
    }
  }

  /** Replaces the model with `model` and takes it as the saved state. */
  private adopt(model: CreatorModel): void {
    this.model.set(model);
    this.baseline.set(JSON.stringify(model));
    this.creatorForm().reset();
    this.attempted.set(false);
  }
}
