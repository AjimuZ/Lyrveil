// The worlds linked to one character, scenario or conversation, with the controls to link another and to unlink one.
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiError, toApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey } from '../../core/i18n/translator';
import { LoreLinks } from './lore-links';
import { LinkKind, LoreBook, TargetLink } from './lore.model';

const NOTE_KEYS = {
  character: 'lore.links.note.character',
  scenario: 'lore.links.note.scenario',
  conversation: 'lore.links.note.conversation',
} as const satisfies Record<LinkKind, TextKey>;

/**
 * Lists the worlds linked to the target named by `kind` and `targetId`, each with a link to its editor and **Scollega**,
 * and offers **Collega** with a choice among the active worlds not yet linked. A note says when a change takes effect:
 * a character's worlds reach every one of its conversations, a conversation's from its next reply, and a scenario's only
 * the conversations that start afterwards. It reads again when `targetId` changes. Its element ids are fixed, so a page
 * holds one instance. A failed read or change shows the
 * server's reason and keeps the list as it was.
 */
@Component({
  selector: 'lyr-world-links',
  imports: [TranslatePipe, RouterLink],
  templateUrl: './world-links.html',
  styleUrl: './world-links.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WorldLinks {
  private readonly links = inject(LoreLinks);
  private latestLoad = 0;

  readonly kind = input.required<LinkKind>();
  readonly targetId = input.required<string>();

  protected readonly linked = signal<readonly TargetLink[]>([]);
  protected readonly worlds = signal<readonly LoreBook[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal<ApiError | null>(null);
  protected readonly busy = signal(false);
  /** The id of the world chosen in the picker, or an empty string. */
  protected readonly chosen = signal('');

  protected readonly note = computed(() => NOTE_KEYS[this.kind()]);
  /** The active worlds that are not linked yet. */
  protected readonly available = computed(() => {
    const taken = new Set(this.linked().map((link) => link.book_id));
    return this.worlds().filter((world) => !taken.has(world.id));
  });

  constructor() {
    effect(() => {
      const kind = this.kind();
      const targetId = this.targetId();
      untracked(() => void this.load(kind, targetId));
    });
  }

  protected async add(): Promise<void> {
    const book = this.chosen();
    if (book === '' || this.busy()) {
      return;
    }
    if (await this.change(() => this.links.add(book, this.kind(), this.targetId()))) {
      this.chosen.set('');
    }
  }

  protected async remove(link: TargetLink): Promise<void> {
    if (this.busy()) {
      return;
    }
    await this.change(() => this.links.remove(link.id));
  }

  protected pick(event: Event): void {
    this.chosen.set((event.target as HTMLSelectElement).value);
  }

  /** Runs a change and reads the list again. Returns whether the server did it. */
  private async change(work: () => Promise<unknown>): Promise<boolean> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await work();
    } catch (failure) {
      this.error.set(toApiError(failure));
      this.busy.set(false);
      return false;
    }
    try {
      await this.load(this.kind(), this.targetId());
      return true;
    } finally {
      this.busy.set(false);
    }
  }

  /** Reads the linked worlds and the active ones; a read that a newer one overtook is dropped. */
  private async load(kind: LinkKind, targetId: string): Promise<void> {
    const ticket = ++this.latestLoad;
    this.loading.set(true);
    try {
      const [linked, worlds] = await Promise.all([this.links.ofTarget(kind, targetId), this.links.activeWorlds()]);
      if (ticket === this.latestLoad) {
        this.linked.set(linked);
        this.worlds.set(worlds);
        this.error.set(null);
      }
    } catch (failure) {
      if (ticket === this.latestLoad) {
        this.error.set(toApiError(failure));
      }
    } finally {
      if (ticket === this.latestLoad) {
        this.loading.set(false);
      }
    }
  }
}
