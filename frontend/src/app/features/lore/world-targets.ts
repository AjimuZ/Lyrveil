// The characters, scenarios and conversations a world is linked to, with the controls to link another and to unlink one.
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { ApiClient } from '../../core/api/api-client';
import { ApiError, toApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey } from '../../core/i18n/translator';
import { CharacterList, ConversationList } from '../chats/chats.model';
import { ScenarioList } from '../scenarios/scenario.model';
import { LoreLinks } from './lore-links';
import { LINK_KINDS, LinkKind, LoreLink, targetOf } from './lore.model';

/** One thing a link can point at, as the picker lists it. */
interface Candidate {
  readonly kind: LinkKind;
  readonly id: string;
  readonly label: string;
}

/** One row of the list of links: the link, what it points at and where that is edited (null when it has no screen). */
interface TargetRow {
  readonly link: LoreLink;
  readonly kind: LinkKind;
  readonly targetId: string;
  /** The target's name, or an empty string when it is not in the lists the picker reads. */
  readonly label: string;
  readonly route: readonly string[] | null;
}

const KIND_KEYS = {
  character: 'lore.targets.kind.character',
  scenario: 'lore.targets.kind.scenario',
  conversation: 'lore.targets.kind.conversation',
} as const satisfies Record<LinkKind, TextKey>;

/**
 * The links of the world named by `bookId`. Each row says what the world is linked to (a character, a scenario or a
 * conversation, by name) and has **Scollega**; below, **Collega** links the world to one more target chosen by kind
 * and name. A target that is no longer listed (archived, say) still shows its row, under a generic name, so the link can
 * be removed. A failed read or change shows the server's reason and keeps the rows as they were.
 */
@Component({
  selector: 'lyr-world-targets',
  imports: [TranslatePipe, RouterLink],
  templateUrl: './world-targets.html',
  styleUrl: './world-targets.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WorldTargets implements OnInit {
  private readonly api = inject(ApiClient);
  private readonly links = inject(LoreLinks);

  readonly bookId = input.required<string>();

  protected readonly kinds = LINK_KINDS.map((kind) => ({ kind, label: KIND_KEYS[kind] }));
  protected readonly linked = signal<readonly LoreLink[]>([]);
  protected readonly candidates = signal<readonly Candidate[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal<ApiError | null>(null);
  protected readonly busy = signal(false);
  protected readonly kind = signal<LinkKind>('character');
  /** The id chosen in the picker, or an empty string. */
  protected readonly chosen = signal('');

  protected readonly rows = computed<readonly TargetRow[]>(() =>
    this.linked().flatMap((link) => {
      const target = targetOf(link);
      if (target === null) {
        return [];
      }
      const known = this.candidates().find((item) => item.kind === target.kind && item.id === target.id);
      return [
        { link, kind: target.kind, targetId: target.id, label: known?.label ?? '', route: routeOf(target.kind, target.id) },
      ];
    }),
  );

  /** The targets of the chosen kind that the world is not linked to yet. */
  protected readonly available = computed(() => {
    const taken = new Set(this.rows().map((row) => `${row.kind}:${row.targetId}`));
    return this.candidates().filter((item) => item.kind === this.kind() && !taken.has(`${item.kind}:${item.id}`));
  });

  ngOnInit(): void {
    void this.load();
  }

  protected kindKey(kind: LinkKind): TextKey {
    return KIND_KEYS[kind];
  }

  protected chooseKind(event: Event): void {
    this.kind.set((event.target as HTMLSelectElement).value as LinkKind);
    this.chosen.set('');
  }

  protected pick(event: Event): void {
    this.chosen.set((event.target as HTMLSelectElement).value);
  }

  protected async add(): Promise<void> {
    const id = this.chosen();
    if (id === '' || this.busy()) {
      return;
    }
    if (await this.change(() => this.links.add(this.bookId(), this.kind(), id))) {
      this.chosen.set('');
    }
  }

  protected async remove(row: TargetRow): Promise<void> {
    if (!this.busy()) {
      await this.change(() => this.links.remove(row.link.id));
    }
  }

  /** Runs a change and reads the links again. Returns whether the server did it. */
  private async change(work: () => Promise<unknown>): Promise<boolean> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await work();
      this.linked.set(await this.links.ofBook(this.bookId()));
      return true;
    } catch (failure) {
      this.error.set(toApiError(failure));
      return false;
    } finally {
      this.busy.set(false);
    }
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    try {
      const [linked, characters, scenarios, conversations] = await Promise.all([
        this.links.ofBook(this.bookId()),
        firstValueFrom(this.api.get<CharacterList>('/characters')),
        firstValueFrom(this.api.get<ScenarioList>('/scenarios', { status: 'active' })),
        firstValueFrom(this.api.get<ConversationList>('/conversations')),
      ]);
      this.linked.set(linked);
      this.candidates.set([
        ...characters.items.map((item): Candidate => ({ kind: 'character', id: item.id, label: item.full_name })),
        ...scenarios.items.map((item): Candidate => ({ kind: 'scenario', id: item.id, label: item.title })),
        ...conversations.items.map((item): Candidate => ({ kind: 'conversation', id: item.id, label: item.title })),
      ]);
      this.error.set(null);
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.loading.set(false);
    }
  }
}

/** The screen that shows a target, or null for a scenario, which has no screen of its own. */
function routeOf(kind: LinkKind, id: string): readonly string[] | null {
  switch (kind) {
    case 'character':
      return ['/characters', id];
    case 'conversation':
      return ['/chats', id];
    case 'scenario':
      return null;
  }
}
