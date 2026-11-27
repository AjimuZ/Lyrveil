// The import and export actions of the Creator's header: a card file or a native file in, V3 JSON, V2 JSON, PNG, the
// native format and the original card out.
import { ChangeDetectionStrategy, Component, effect, inject, input, signal, untracked } from '@angular/core';
import { Router } from '@angular/router';

import { ApiError, toApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey } from '../../core/i18n/translator';
import { CardFormat, isNativeCharacterFile } from './card-import.model';
import { CharactersStore, DownloadedFile, ScenarioChoice } from './characters-store';
import { saveFile } from './save-file';

/** The export buttons of the card formats, in the order they are drawn. */
const CARD_FORMATS: readonly { readonly format: CardFormat; readonly label: TextKey }[] = [
  { format: 'v3json', label: 'card.export.v3' },
  { format: 'v2json', label: 'card.export.v2' },
  { format: 'png', label: 'card.export.png' },
];

/**
 * **Importa una card** takes a Character Card (JSON or PNG) or a `lyrveil.character` file. A card opens the import
 * preview in the Creator (`/characters/import/:importId`) and writes nothing; a native file creates the character at
 * once, as the native import always did, and opens it. With `characterId` the **Esporta** panel adds the three card
 * formats with the scenarios to include as greetings, the native format and, for an imported character, **Scarica la
 * card originale**. A refusal shows the server's Italian reason, which names the card path of a refused text.
 */
@Component({
  selector: 'lyr-card-actions',
  imports: [TranslatePipe],
  templateUrl: './card-actions.html',
  styleUrl: './card-actions.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CardActions {
  private readonly store = inject(CharactersStore);
  private readonly router = inject(Router);

  /** The stored character whose export is offered; absent on a screen with no stored character. */
  readonly characterId = input<string>();

  protected readonly formats = CARD_FORMATS;
  protected readonly busy = signal(false);
  protected readonly error = signal<ApiError | null>(null);
  protected readonly noAvatar = signal(false);
  protected readonly open = signal(false);
  protected readonly scenarios = signal<readonly ScenarioChoice[]>([]);
  protected readonly chosen = signal<ReadonlySet<string>>(new Set());
  /** The stored source of an imported character; null for one that was not imported. */
  protected readonly original = signal<DownloadedFile | null>(null);

  constructor() {
    // External sync: another character closes the panel and forgets what it read of the previous one.
    effect(() => {
      this.characterId();
      untracked(() => {
        this.open.set(false);
        this.scenarios.set([]);
        this.chosen.set(new Set());
        this.original.set(null);
      });
    });
  }

  /** Reads the picked file: a card goes to the preview, a native file creates the character. */
  protected async pick(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.item(0) ?? null;
    input.value = '';
    if (file === null) {
      return;
    }
    await this.run(async () => {
      if (file.name.toLowerCase().endsWith('.json') && isNativeCharacterFile(await file.text())) {
        const character = await this.store.importNative(file);
        await this.router.navigate(['/characters', character.id, 'edit']);
      } else {
        const imported = await this.store.importCard(file);
        await this.router.navigate(['/characters/import', imported.id]);
      }
    });
  }

  /** Opens or closes the export panel; opening reads the scenarios to choose and whether a source card is stored. */
  protected async toggle(): Promise<void> {
    if (this.open()) {
      this.open.set(false);
      return;
    }
    const id = this.characterId();
    if (id === undefined) {
      return;
    }
    this.open.set(true);
    this.error.set(null);
    this.noAvatar.set(false);
    try {
      this.scenarios.set(await this.store.scenariosOf(id));
    } catch (failure) {
      this.error.set(toApiError(failure));
    }
    try {
      this.original.set(await this.store.originalCard(id));
    } catch {
      // A 404 means the character was not imported, so there is no original to offer.
      this.original.set(null);
    }
  }

  protected isChosen(id: string): boolean {
    return this.chosen().has(id);
  }

  protected choose(id: string, event: Event): void {
    const next = new Set(this.chosen());
    if ((event.target as HTMLInputElement).checked) {
      next.add(id);
    } else {
      next.delete(id);
    }
    this.chosen.set(next);
  }

  /** Exports in the order the scenarios are listed, so the first one chosen gives the first message. */
  protected async exportCard(format: CardFormat): Promise<void> {
    const id = this.characterId();
    if (id === undefined) {
      return;
    }
    const ids = this.scenarios()
      .map((scenario) => scenario.id)
      .filter((scenarioId) => this.chosen().has(scenarioId));
    await this.run(async () => saveFile(await this.store.exportCard(id, format, ids)), format === 'png');
  }

  protected async exportNative(): Promise<void> {
    const id = this.characterId();
    if (id !== undefined) {
      await this.run(async () => saveFile(await this.store.exportNative(id)));
    }
  }

  protected saveOriginal(): void {
    const file = this.original();
    if (file !== null) {
      saveFile(file);
    }
  }

  /** Runs `work` once at a time. A failure becomes `error`; a 409 on a PNG means the character has no avatar. */
  private async run(work: () => Promise<void>, png = false): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.noAvatar.set(false);
    try {
      await work();
    } catch (failure) {
      const problem = toApiError(failure);
      if (png && problem.code === 'CONFLICT') {
        this.noAvatar.set(true);
      } else {
        this.error.set(problem);
      }
    } finally {
      this.busy.set(false);
    }
  }
}
