// Screen 12 of docs/frontend.md: **Le tue persone**, the list of personas and the form that creates or edits one.
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, Translator } from '../../core/i18n/translator';
import { ObEmptyState } from '../../shared/ui/empty-state';
import { ObSkeleton } from '../../shared/ui/skeleton';
import { GrammaticalGender } from '../chats/chats.model';
import { GENDER_OPTIONS } from '../chats/new-chat-dialog';
import { PERSONA_LIMITS, Persona, PersonaStatus } from './persona.model';
import { PersonasStore } from './personas-store';

interface Tab {
  readonly status: PersonaStatus;
  readonly label: TextKey;
}

/**
 * The personas. A row shows the name, **Predefinita** on the default one, and the number of conversations that use it;
 * it has edit, **Rendi predefinita** (an active persona that is not the default), archive or restore (never for the
 * default) and delete with an inline confirmation, offered only for a persona that no conversation uses. The form
 * (name, how to address the user, optional age of at least 18, appearance, about) creates a persona or replaces the
 * fields of the one being edited; a refusal of the server stays on screen with its reason. The form warns that what is
 * written in it is known by every character that talks with the persona (ADR 0048).
 */
@Component({
  selector: 'lyr-personas-screen',
  imports: [TranslatePipe, ObEmptyState, ObSkeleton],
  templateUrl: './personas-screen.html',
  styleUrl: './personas-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PersonasScreen implements OnInit {
  protected readonly store = inject(PersonasStore);
  private readonly translator = inject(Translator);

  protected readonly tabs: readonly Tab[] = [
    { status: 'active', label: 'personas.tab.active' },
    { status: 'archived', label: 'personas.tab.archived' },
  ];
  protected readonly genders = GENDER_OPTIONS;
  protected readonly limits = PERSONA_LIMITS;

  /** Whether the form is open: `'new'`, the id of the persona being edited, or null. */
  protected readonly editing = signal<string | null>(null);
  /** The id of the persona whose delete is waiting for a confirmation, or null. */
  protected readonly confirmingDelete = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly attempted = signal(false);

  protected readonly name = signal('');
  protected readonly gender = signal<GrammaticalGender>('non_indicato');
  protected readonly age = signal('');
  protected readonly appearance = signal('');
  protected readonly about = signal('');

  protected readonly nameValid = computed(() => {
    const length = this.name().trim().length;
    return length >= 1 && length <= PERSONA_LIMITS.name;
  });
  /** The age is optional: blank is valid, otherwise a whole number from 18 to 120. */
  protected readonly ageValid = computed(() => {
    const text = this.age().trim();
    if (text === '') {
      return true;
    }
    const value = Number(text);
    return /^\d+$/.test(text) && value >= PERSONA_LIMITS.minAge && value <= PERSONA_LIMITS.maxAge;
  });

  ngOnInit(): void {
    this.store.clearActionError();
    void this.store.showTab(this.store.tab());
  }

  protected newPersona(): void {
    this.fill(null);
    this.editing.set('new');
  }

  protected edit(persona: Persona): void {
    this.fill(persona);
    this.editing.set(persona.id);
  }

  protected cancel(): void {
    this.editing.set(null);
    this.store.clearActionError();
  }

  protected async submit(event: Event): Promise<void> {
    event.preventDefault();
    this.attempted.set(true);
    const target = this.editing();
    if (target === null || !this.nameValid() || !this.ageValid() || this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      const age = this.age().trim();
      const saved = await this.store.save(target === 'new' ? null : target, {
        name: this.name().trim(),
        grammatical_gender: this.gender(),
        age: age === '' ? null : Number(age),
        appearance: this.appearance().trim(),
        about: this.about().trim(),
      });
      if (saved !== null) {
        this.editing.set(null);
      }
    } finally {
      this.busy.set(false);
    }
  }

  protected async toggleArchive(persona: Persona): Promise<void> {
    this.confirmingDelete.set(null);
    await this.store.toggleArchive(persona);
  }

  protected async makeDefault(persona: Persona): Promise<void> {
    this.confirmingDelete.set(null);
    await this.store.makeDefault(persona);
  }

  protected async confirmRemove(persona: Persona): Promise<void> {
    this.confirmingDelete.set(null);
    const removed = await this.store.remove(persona);
    if (removed && this.editing() === persona.id) {
      this.editing.set(null);
    }
  }

  /** "1 conversazione" or "N conversazioni": how many chats use the persona. */
  protected usedBy(persona: Persona): string {
    return this.translator.plural('personas.usedBy', persona.conversation_count);
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement).value;
  }

  protected asGender(value: string): GrammaticalGender {
    return GENDER_OPTIONS.find((option) => option.value === value)?.value ?? 'non_indicato';
  }

  private fill(persona: Persona | null): void {
    this.store.clearActionError();
    this.attempted.set(false);
    this.name.set(persona?.name ?? '');
    this.gender.set(persona?.grammatical_gender ?? 'non_indicato');
    this.age.set(String(persona?.age ?? ''));
    this.appearance.set(persona?.appearance ?? '');
    this.about.set(persona?.about ?? '');
  }
}
