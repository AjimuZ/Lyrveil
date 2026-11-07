// Screen 4 of docs/frontend.md: the character profile, with its versions (compare and restore) and the duplicate,
// archive and purge actions.
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { ApiError, toApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, TextParams, Translator } from '../../core/i18n/translator';
import { Conversation } from '../chats/chats.model';
import { GENDER_OPTIONS, LANGUAGE_OPTIONS } from '../chats/new-chat-dialog';
import { PurgePreview, StoredCharacter, VersionDiff, VersionSummary } from './card.model';
import { CharactersStore } from './characters-store';
import { Option, TRAIT_OPTIONS } from './creator-options';
import { fieldLabelKey, formatValue } from './version-diff';

type Phase = 'loading' | 'ready' | 'failed';

interface Notice {
  readonly key: TextKey;
  readonly params?: TextParams;
}

/** One line of the identity summary: the value is written text, or the catalog text `valueKey` for a closed choice. */
interface IdentityRow {
  readonly label: TextKey;
  readonly text: string;
  readonly valueKey: TextKey | null;
}

const ACTOR_LABEL: Readonly<Record<string, TextKey>> = {
  user: 'profile.actor.user',
  import: 'profile.actor.import',
  restore: 'profile.actor.restore',
  duplicate: 'profile.actor.duplicate',
  proposal: 'profile.actor.proposal',
};

function labelOf(options: readonly Option[], value: string): TextKey | null {
  return options.find((option) => option.value === value)?.label ?? null;
}

/**
 * Shows `/characters/:id`. The screen reads the character, its versions and its active conversations, and every
 * action goes through the REST API and then reads again, because the server is the source of truth. A purge needs the
 * first name typed after a preview of what it deletes; the server checks the name again. A refused action shows the
 * server's explanation and changes nothing.
 *
 * Not delivered here, so no control is drawn for it: the avatar and its variants (P03.3), the memory studio and the
 * proposals for the profile.
 */
@Component({
  selector: 'lyr-character-profile',
  imports: [TranslatePipe, RouterLink],
  templateUrl: './character-profile-screen.html',
  styleUrl: './character-profile-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CharacterProfileScreen {
  private readonly store = inject(CharactersStore);
  private readonly router = inject(Router);
  private readonly translator = inject(Translator);
  private latestLoad = 0;

  /** The character to show, from the route. A new id (after a duplicate) loads that character. */
  readonly id = input.required<string>();

  protected readonly phase = signal<Phase>('loading');
  protected readonly loadError = signal<ApiError | null>(null);
  protected readonly actionError = signal<ApiError | null>(null);
  protected readonly busy = signal(false);
  protected readonly notice = signal<Notice | null>(null);

  protected readonly character = signal<StoredCharacter | null>(null);
  protected readonly versions = signal<readonly VersionSummary[]>([]);
  protected readonly conversations = signal<readonly Conversation[]>([]);
  /** The comparison on show: a chosen version against the current one. */
  protected readonly comparison = signal<VersionDiff | null>(null);

  protected readonly purging = signal<PurgePreview | null>(null);
  protected readonly purgeName = signal('');

  protected readonly archived = computed(() => this.character()?.status === 'archived');
  protected readonly firstName = computed(() => this.character()?.card.identity.first_name ?? '');
  protected readonly initial = computed(() => this.firstName().slice(0, 1).toUpperCase());
  protected readonly canPurge = computed(() => this.purgeName().trim() === this.firstName() && !this.busy());

  protected readonly identityRows = computed<readonly IdentityRow[]>(() => {
    const identity = this.character()?.card.identity;
    if (identity === undefined) {
      return [];
    }
    const gender = labelOf(GENDER_OPTIONS, identity.grammatical_gender);
    const language = labelOf(LANGUAGE_OPTIONS, identity.primary_language);
    const rows: IdentityRow[] = [{ label: 'creator.age', text: String(identity.age), valueKey: null }];
    if (gender !== null) {
      rows.push({ label: 'creator.gender', text: '', valueKey: gender });
    }
    if (language !== null) {
      rows.push({ label: 'creator.language', text: '', valueKey: language });
    }
    const written: [TextKey, string][] = [
      ['creator.occupation', identity.occupation],
      ['creator.setting', identity.setting],
      ['creator.narrativeIdentity', identity.narrative_identity],
      ['creator.biography', identity.biography],
      ['creator.background', identity.background],
      ['creator.interests', identity.interests.join(', ')],
    ];
    for (const [label, text] of written) {
      if (text.trim() !== '') {
        rows.push({ label, text, valueKey: null });
      }
    }
    return rows;
  });

  protected readonly purgeLines = computed<readonly string[]>(() => {
    const preview = this.purging();
    if (preview === null) {
      return [];
    }
    return [
      this.translator.plural('profile.purge.conversations', preview.conversations),
      this.translator.plural('profile.purge.messages', preview.messages),
      this.translator.plural('profile.purge.versions', preview.versions),
    ];
  });

  private readonly purgeInput = viewChild<ElementRef<HTMLInputElement>>('purgeInput');

  constructor() {
    // External sync: the data on screen follows the route's id, and the same component serves the next character.
    effect(() => {
      const id = this.id();
      untracked(() => void this.load(id));
    });
    // External sync: the confirmation opens with the cursor in the name field.
    effect(() => this.purgeInput()?.nativeElement.focus());
  }

  protected traitLabel(category: string): TextKey | null {
    return labelOf(TRAIT_OPTIONS, category);
  }

  protected actorLabel(actor: string): TextKey | null {
    return ACTOR_LABEL[actor] ?? null;
  }

  protected fieldKey(path: string): TextKey | null {
    return fieldLabelKey(path);
  }

  protected show(value: unknown): string {
    return formatValue(value);
  }

  protected when(iso: string): string {
    const date = new Date(iso);
    return Number.isNaN(date.getTime())
      ? iso
      : new Intl.DateTimeFormat(this.translator.locale(), { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement).value;
  }

  // Actions. Each one clears the previous message, runs, and shows either a notice or the server's reason.

  protected async startChat(): Promise<void> {
    await this.run(async () => {
      const created = await this.store.startChat(this.id());
      await this.router.navigate(['/chats', created.id]);
    });
  }

  protected async duplicate(): Promise<void> {
    await this.run(async () => {
      const copy = await this.store.duplicate(this.id());
      await this.router.navigate(['/characters', copy.id]);
      this.notice.set({ key: 'profile.notice.duplicated' });
    });
  }

  protected async toggleArchive(): Promise<void> {
    const archive = !this.archived();
    await this.run(async () => {
      await (archive ? this.store.archive(this.id()) : this.store.unarchive(this.id()));
      await this.refresh();
      this.notice.set({ key: archive ? 'profile.notice.archived' : 'profile.notice.unarchived' });
    });
  }

  protected async compare(version: VersionSummary): Promise<void> {
    const current = this.character()?.current_version_no;
    if (current === undefined) {
      return;
    }
    await this.run(async () => {
      this.comparison.set(await this.store.diff(this.id(), version.version_no, current));
    });
  }

  protected closeComparison(): void {
    this.comparison.set(null);
  }

  protected async restore(version: VersionSummary): Promise<void> {
    await this.run(async () => {
      const restored = await this.store.restore(this.id(), version.version_no);
      await this.refresh();
      this.notice.set({
        key: 'profile.notice.restored',
        params: { from: version.version_no, to: restored.current_version_no },
      });
    });
  }

  /** Reads what a purge deletes and opens the confirmation. */
  protected async startPurge(): Promise<void> {
    await this.run(async () => {
      this.purging.set(await this.store.purgePreview(this.id()));
      this.purgeName.set('');
    });
  }

  protected cancelPurge(): void {
    this.purging.set(null);
    this.purgeName.set('');
  }

  protected async confirmPurge(event: Event): Promise<void> {
    event.preventDefault();
    if (!this.canPurge()) {
      return;
    }
    await this.run(async () => {
      await this.store.purge(this.id(), this.purgeName().trim());
      this.purging.set(null);
      await this.router.navigate(['/chats']);
    });
  }

  /** Runs `work` once at a time. A failure becomes `actionError`; the screen keeps what it showed. */
  private async run(work: () => Promise<void>): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    this.actionError.set(null);
    this.notice.set(null);
    try {
      await work();
    } catch (failure) {
      this.actionError.set(toApiError(failure));
    } finally {
      this.busy.set(false);
    }
  }

  private async load(id: string): Promise<void> {
    const ticket = ++this.latestLoad;
    this.phase.set('loading');
    this.character.set(null);
    this.comparison.set(null);
    this.purging.set(null);
    try {
      const [character, versions, conversations] = await Promise.all([
        this.store.load(id),
        this.store.versions(id),
        this.store.conversations(id),
      ]);
      if (ticket !== this.latestLoad) {
        return;
      }
      this.character.set(character);
      this.versions.set(versions);
      this.conversations.set(conversations);
      this.phase.set('ready');
    } catch (failure) {
      if (ticket === this.latestLoad) {
        this.loadError.set(toApiError(failure));
        this.phase.set('failed');
      }
    }
  }

  /** Reads the character and its versions again after an action changed them, and closes a stale comparison. */
  private async refresh(): Promise<void> {
    const id = this.id();
    const [character, versions] = await Promise.all([this.store.load(id), this.store.versions(id)]);
    this.character.set(character);
    this.versions.set(versions);
    this.comparison.set(null);
  }
}
