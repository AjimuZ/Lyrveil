// Dialog that starts a chat: pick a character and a language, or fill the minimal character form first.
import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiError, toApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey } from '../../core/i18n/translator';
import { PERSONA_LIMITS, Persona } from '../personas/persona.model';
import { PersonasStore } from '../personas/personas-store';
import { Scenario } from '../scenarios/scenario.model';
import { ScenariosStore } from '../scenarios/scenarios-store';
import { ChatsStore } from './chats-store';
import { Character, GrammaticalGender, LANGUAGE_CODES } from './chats.model';

interface Option<T extends string> {
  readonly value: T;
  readonly label: TextKey;
}

export const GENDER_OPTIONS: readonly Option<GrammaticalGender>[] = [
  { value: 'non_indicato', label: 'character.gender.non_indicato' },
  { value: 'femminile', label: 'character.gender.femminile' },
  { value: 'maschile', label: 'character.gender.maschile' },
];

const LANGUAGE_LABELS = {
  it: 'language.it',
  en: 'language.en',
  fr: 'language.fr',
  de: 'language.de',
  es: 'language.es',
} as const satisfies Record<(typeof LANGUAGE_CODES)[number], TextKey>;

export const LANGUAGE_OPTIONS: readonly Option<string>[] = LANGUAGE_CODES.map((value) => ({
  value,
  label: LANGUAGE_LABELS[value],
}));

const MAX_NAME_LENGTH = 80;
const MIN_AGE = 18;
const MAX_AGE = 120;

type Step = 'pick' | 'create';

/**
 * A modal `<dialog>`. `open()` shows it and loads the characters; the user picks one and starts the chat, or creates a
 * character with the minimal form (first name, optional last name, age, how it speaks of itself, language), which
 * then becomes the selected one. A link opens the full Creator (`/characters/new`) for everything the minimal form
 * leaves out; the form stays for the quick start. A start or a save that the server refuses
 * keeps the dialog open and shows the reason; success on a start closes it, and the new row appears in the list.
 *
 * The picker also asks **Persona** (active personas only, so an archived one is never offered) and **Cosa sa di te
 * all'inizio**. The persona is preselected: the scenario's, when the chosen scenario names an offered one, otherwise
 * the default; until the user picks one by hand, choosing another scenario moves it again. The chat's persona is fixed
 * at the start, which is why this is the only place that chooses it.
 */
@Component({
  selector: 'lyr-new-chat-dialog',
  imports: [TranslatePipe, RouterLink],
  templateUrl: './new-chat-dialog.html',
  styleUrl: './new-chat-dialog.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NewChatDialog {
  private readonly store = inject(ChatsStore);
  private readonly scenarioStore = inject(ScenariosStore);
  private readonly personaStore = inject(PersonasStore);
  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  protected readonly genders = GENDER_OPTIONS;
  protected readonly languages = LANGUAGE_OPTIONS;

  protected readonly step = signal<Step>('pick');
  protected readonly loading = signal(false);
  protected readonly busy = signal(false);
  protected readonly characters = signal<readonly Character[]>([]);
  protected readonly error = signal<ApiError | null>(null);

  protected readonly characterId = signal('');
  protected readonly language = signal('');
  protected readonly scenarios = signal<readonly Scenario[]>([]);
  protected readonly scenarioId = signal('');
  /** The scenarios the chosen character can use: those for it and those for any character. */
  protected readonly choices = computed(() =>
    this.scenarios().filter((scenario) => scenario.character_id === null || scenario.character_id === this.characterId()),
  );

  /** The active personas, the default first. Empty when they could not be loaded: the server then picks the default. */
  protected readonly personas = signal<readonly Persona[]>([]);
  protected readonly personaId = signal('');
  protected readonly personaNote = signal('');
  protected readonly noteLimit = PERSONA_LIMITS.note;
  /** True once the user chose a persona by hand, so a later scenario choice no longer moves it. */
  private personaChosen = false;

  protected readonly firstName = signal('');
  protected readonly lastName = signal('');
  protected readonly age = signal('');
  protected readonly gender = signal<GrammaticalGender>('non_indicato');
  protected readonly primaryLanguage = signal('it');
  protected readonly attempted = signal(false);

  protected readonly firstNameValid = computed(() => {
    const length = this.firstName().trim().length;
    return length >= 1 && length <= MAX_NAME_LENGTH;
  });
  protected readonly lastNameValid = computed(() => this.lastName().trim().length <= MAX_NAME_LENGTH);
  protected readonly ageValid = computed(() => {
    const text = this.age().trim();
    const value = Number(text);
    return /^\d+$/.test(text) && value >= MIN_AGE && value <= MAX_AGE;
  });

  /**
   * Shows the dialog on the picker, or on the character form when no character exists yet. `scenarioId` chooses a
   * scenario in advance, and its character too when the scenario is for one; a scenario that is no longer active is
   * ignored. The scenarios are optional: a failure to load them leaves the picker without that choice.
   */
  async open(scenarioId: string | null = null): Promise<void> {
    this.error.set(null);
    this.characterId.set('');
    this.language.set('');
    this.scenarioId.set('');
    this.scenarios.set([]);
    this.personas.set([]);
    this.personaId.set('');
    this.personaNote.set('');
    this.personaChosen = false;
    this.resetForm();
    this.step.set('pick');
    this.loading.set(true);
    this.dialog().nativeElement.showModal();
    try {
      const [found, offered, offeredPersonas] = await Promise.all([
        this.store.listCharacters(),
        this.scenarioStore.listActive().catch((): readonly Scenario[] => []),
        this.personaStore.listActive().catch((): readonly Persona[] => []),
      ]);
      this.characters.set(found);
      this.scenarios.set(offered);
      this.personas.set(offeredPersonas);
      const preselected = offered.find((scenario) => scenario.id === scenarioId);
      const forCharacter = found.find((character) => character.id === preselected?.character_id);
      this.characterId.set(forCharacter?.id ?? found[0]?.id ?? '');
      if (preselected !== undefined && this.choices().includes(preselected)) {
        this.scenarioId.set(preselected.id);
      }
      this.preselectPersona();
      // Only an empty list changes the step. showModal can paint the previous view before the loading state, and a
      // click on "new character" there must not be undone when the list arrives.
      if (found.length === 0) {
        this.step.set('create');
      }
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.loading.set(false);
    }
  }

  protected close(): void {
    this.dialog().nativeElement.close();
  }

  protected showForm(): void {
    this.error.set(null);
    this.resetForm();
    this.step.set('create');
  }

  protected backToPicker(): void {
    this.error.set(null);
    this.step.set('pick');
  }

  protected async start(): Promise<void> {
    if (this.characterId() === '' || this.busy()) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.store.startChat(
        this.characterId(),
        this.language() || null,
        this.scenarioId() || null,
        this.personaId() || null,
        this.personaNote(),
      );
      this.close();
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.busy.set(false);
    }
  }

  protected async saveCharacter(event: Event): Promise<void> {
    event.preventDefault();
    this.attempted.set(true);
    if (!this.firstNameValid() || !this.lastNameValid() || !this.ageValid() || this.busy()) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      const created = await this.store.createCharacter({
        first_name: this.firstName().trim(),
        last_name: this.lastName().trim() || null,
        age: Number(this.age().trim()),
        grammatical_gender: this.gender(),
        primary_language: this.primaryLanguage(),
      });
      this.characters.update((current) => [created, ...current]);
      this.chooseCharacter(created.id);
      this.step.set('pick');
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.busy.set(false);
    }
  }

  /** Chooses a character and drops the chosen scenario when that character cannot use it. */
  protected chooseCharacter(id: string): void {
    this.characterId.set(id);
    if (!this.choices().some((scenario) => scenario.id === this.scenarioId())) {
      this.scenarioId.set('');
    }
    this.preselectPersona();
  }

  /** Chooses a scenario (empty for none); the persona follows it until the user picks one by hand. */
  protected chooseScenario(id: string): void {
    this.scenarioId.set(id);
    this.preselectPersona();
  }

  /** Records a persona the user picked, which later scenario choices leave alone. */
  protected choosePersona(id: string): void {
    this.personaChosen = true;
    this.personaId.set(id);
  }

  /**
   * Sets the persona to the chosen scenario's when that persona is offered (active), else to the default, unless the
   * user already picked one. The server applies the same rule when no persona is sent.
   */
  private preselectPersona(): void {
    if (this.personaChosen) {
      return;
    }
    const scenario = this.scenarios().find((item) => item.id === this.scenarioId());
    const named = this.personas().find((persona) => persona.id === scenario?.persona_id);
    const fallback = this.personas().find((persona) => persona.is_default) ?? this.personas()[0];
    this.personaId.set(named?.id ?? fallback?.id ?? '');
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement).value;
  }

  protected asGender(value: string): GrammaticalGender {
    return GENDER_OPTIONS.find((option) => option.value === value)?.value ?? 'non_indicato';
  }

  protected closed(): void {
    this.error.set(null);
    this.busy.set(false);
  }

  private resetForm(): void {
    this.firstName.set('');
    this.lastName.set('');
    this.age.set('');
    this.gender.set('non_indicato');
    this.primaryLanguage.set('it');
    this.attempted.set(false);
  }
}
