// Screen 7 of docs/frontend.md: the scenario list with search, an archived tab, the edit form, and the start of a chat.
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { ObEmptyState } from '../../shared/ui/empty-state';
import { ObSkeleton } from '../../shared/ui/skeleton';
import { TextKey } from '../../core/i18n/translator';
import { Character, LANGUAGE_CODES } from '../chats/chats.model';
import { SCENARIO_LIMITS, Scenario, ScenarioStatus } from './scenario.model';
import { ScenariosStore } from './scenarios-store';

interface Tab {
  readonly status: ScenarioStatus;
  readonly label: TextKey;
}

const LANGUAGE_LABELS = {
  it: 'language.it',
  en: 'language.en',
  fr: 'language.fr',
  de: 'language.de',
  es: 'language.es',
} as const satisfies Record<(typeof LANGUAGE_CODES)[number], TextKey>;

/**
 * The scenarios. A row shows the title, the character it is for (or "any"), and has **Inizia conversazione** (a link to
 * the chat list that opens the new-chat picker with this scenario chosen), edit, archive or restore, and delete with an
 * inline confirmation. The form (title, description, setting, opening message, optional character, language) creates
 * a scenario or replaces the fields of the one being edited; a refusal of the server stays on screen with its reason.
 * A note says that conversations already started keep their own copy.
 */
@Component({
  selector: 'lyr-scenarios-screen',
  imports: [RouterLink, TranslatePipe, ObEmptyState, ObSkeleton],
  templateUrl: './scenarios-screen.html',
  styleUrl: './scenarios-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ScenariosScreen implements OnInit {
  protected readonly store = inject(ScenariosStore);

  protected readonly tabs: readonly Tab[] = [
    { status: 'active', label: 'scenarios.tab.active' },
    { status: 'archived', label: 'scenarios.tab.archived' },
  ];
  protected readonly languages = LANGUAGE_CODES.map((value) => ({ value, label: LANGUAGE_LABELS[value] }));
  protected readonly limits = SCENARIO_LIMITS;

  protected readonly characters = signal<readonly Character[]>([]);
  /** Whether the form is open: `'new'`, the id of the scenario being edited, or null. */
  protected readonly editing = signal<string | null>(null);
  /** The id of the scenario whose delete is waiting for a confirmation, or null. */
  protected readonly confirmingDelete = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly attempted = signal(false);

  protected readonly title = signal('');
  protected readonly description = signal('');
  protected readonly setting = signal('');
  protected readonly openingMessage = signal('');
  protected readonly characterId = signal('');
  protected readonly language = signal('');

  protected readonly titleValid = computed(() => {
    const length = this.title().trim().length;
    return length >= 1 && length <= SCENARIO_LIMITS.title;
  });

  ngOnInit(): void {
    this.store.clearActionError();
    void this.store.showTab(this.store.tab());
    void this.store.listCharacters().then(
      (found) => this.characters.set(found),
      () => this.characters.set([]),
    );
  }

  protected newScenario(): void {
    this.fill(null);
    this.editing.set('new');
  }

  protected edit(scenario: Scenario): void {
    this.fill(scenario);
    this.editing.set(scenario.id);
  }

  protected cancel(): void {
    this.editing.set(null);
    this.store.clearActionError();
  }

  protected async submit(event: Event): Promise<void> {
    event.preventDefault();
    this.attempted.set(true);
    const target = this.editing();
    if (target === null || !this.titleValid() || this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      const saved = await this.store.save(target === 'new' ? null : target, {
        title: this.title().trim(),
        description: this.description().trim(),
        setting: this.setting().trim(),
        opening_message: this.openingMessage().trim(),
        character_id: this.characterId() || null,
        language: this.language() || null,
      });
      if (saved !== null) {
        this.editing.set(null);
      }
    } finally {
      this.busy.set(false);
    }
  }

  protected async toggleArchive(scenario: Scenario): Promise<void> {
    this.confirmingDelete.set(null);
    await this.store.toggleArchive(scenario);
  }

  protected async confirmRemove(scenario: Scenario): Promise<void> {
    this.confirmingDelete.set(null);
    const removed = await this.store.remove(scenario);
    if (removed && this.editing() === scenario.id) {
      this.editing.set(null);
    }
  }

  protected search(event: Event): void {
    this.store.search((event.target as HTMLInputElement).value);
  }

  protected characterName(scenario: Scenario): string | null {
    return this.characters().find((character) => character.id === scenario.character_id)?.full_name ?? null;
  }

  /** Returns the catalog key of the scenario's language name, or null when it inherits or the code is unknown. */
  protected languageKey(scenario: Scenario): TextKey | null {
    const known = LANGUAGE_CODES.find((code) => code === scenario.language);
    return known === undefined ? null : LANGUAGE_LABELS[known];
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement).value;
  }

  private fill(scenario: Scenario | null): void {
    this.store.clearActionError();
    this.attempted.set(false);
    this.title.set(scenario?.title ?? '');
    this.description.set(scenario?.description ?? '');
    this.setting.set(scenario?.setting ?? '');
    this.openingMessage.set(scenario?.opening_message ?? '');
    this.characterId.set(scenario?.character_id ?? '');
    this.language.set(scenario?.language ?? '');
  }
}
