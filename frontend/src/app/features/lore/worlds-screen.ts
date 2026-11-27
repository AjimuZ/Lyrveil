// Screen 11 of docs/frontend.md: **Mondi**, the list of worlds with search, the archived tab and the form that creates one.
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, Translator } from '../../core/i18n/translator';
import { ObEmptyState } from '../../shared/ui/empty-state';
import { ObSkeleton } from '../../shared/ui/skeleton';
import { BookField, BookStatus, LORE_LIMITS, LoreBook, bookFieldOf } from './lore.model';
import { WorldsStore } from './worlds-store';

interface Tab {
  readonly status: BookStatus;
  readonly label: TextKey;
}

/**
 * The worlds. A row shows the name, how many entries the world holds and an excerpt of its description; it opens the
 * world's editor, archives or restores it, and deletes it after an inline confirmation. **Nuovo mondo** opens a form
 * for the name and the description; the refusal of the server is shown next to the field it names (the scan depth and
 * the budget are set on the world's own page). A created world opens its editor.
 */
@Component({
  selector: 'lyr-worlds-screen',
  imports: [TranslatePipe, RouterLink, ObEmptyState, ObSkeleton],
  templateUrl: './worlds-screen.html',
  styleUrl: './worlds-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WorldsScreen implements OnInit {
  protected readonly store = inject(WorldsStore);
  private readonly translator = inject(Translator);
  private readonly router = inject(Router);

  protected readonly tabs: readonly Tab[] = [
    { status: 'active', label: 'worlds.tab.active' },
    { status: 'archived', label: 'worlds.tab.archived' },
  ];
  protected readonly limits = LORE_LIMITS;

  protected readonly creating = signal(false);
  /** The id of the world whose delete is waiting for a confirmation, or null. */
  protected readonly confirmingDelete = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly attempted = signal(false);
  protected readonly name = signal('');
  protected readonly description = signal('');

  protected readonly nameValid = computed(() => {
    const length = this.name().trim().length;
    return length >= 1 && length <= LORE_LIMITS.name;
  });
  protected readonly descriptionValid = computed(() => this.description().trim().length <= LORE_LIMITS.description);

  /** The field the server's last refusal named, or null when it named none (it is then shown above the form). */
  protected readonly serverField = computed<BookField | null>(() => bookFieldOf(this.store.actionError()?.detail ?? null));

  ngOnInit(): void {
    this.store.clearActionError();
    void this.store.showTab(this.store.tab());
  }

  protected newWorld(): void {
    this.store.clearActionError();
    this.attempted.set(false);
    this.name.set('');
    this.description.set('');
    this.creating.set(true);
  }

  protected cancel(): void {
    this.creating.set(false);
    this.store.clearActionError();
  }

  protected async submit(event: Event): Promise<void> {
    event.preventDefault();
    this.attempted.set(true);
    if (!this.nameValid() || !this.descriptionValid() || this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      const created = await this.store.create({
        name: this.name().trim(),
        description: this.description().trim(),
        scan_depth: LORE_LIMITS.defaultScanDepth,
        token_budget: null,
      });
      if (created !== null) {
        this.creating.set(false);
        await this.router.navigate(['/worlds', created.id]);
      }
    } finally {
      this.busy.set(false);
    }
  }

  protected async toggleArchive(world: LoreBook): Promise<void> {
    this.confirmingDelete.set(null);
    await this.store.toggleArchive(world);
  }

  protected async confirmRemove(world: LoreBook): Promise<void> {
    this.confirmingDelete.set(null);
    await this.store.remove(world);
  }

  protected search(event: Event): void {
    this.store.search(this.valueOf(event));
  }

  /** "1 voce" or "N voci": how many entries the world holds. */
  protected entriesOf(world: LoreBook): string {
    return this.translator.plural('worlds.entries', world.entry_count);
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLTextAreaElement).value;
  }
}
