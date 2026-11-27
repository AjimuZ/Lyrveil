// The preview of an imported Character Card, above the Creator's form: what the file held, the import report, the
// token estimate, the choice of source, the structured conversion and the scenarios from the greetings.
import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey } from '../../core/i18n/translator';
import { CardImport, ImportSource, MacroUse } from './card-import.model';
import { fieldLabelKey } from './version-diff';

/** The catalog text of each action the import took on a macro of the card. */
const MACRO_ACTION: Readonly<Record<string, TextKey>> = {
  placeholder: 'card.macro.placeholder',
  removed: 'card.macro.removed',
  first_option: 'card.macro.firstOption',
  reversed: 'card.macro.reversed',
};

/** The catalog text of each free text of a card that can be edited outside Lyrveil. */
const CHANGED_TEXT: Readonly<Record<string, TextKey>> = {
  description: 'card.changed.description',
  personality: 'card.changed.personality',
  mes_example: 'card.changed.mesExample',
};

/** The tokens above which the estimate warns that a card may not fit the room of the character's prompt blocks. */
export const LONG_CARD_TOKENS = 1500;

/**
 * Shows the import of `cardImport`. It decides nothing: the Creator owns the form, the chosen `source` and the save, and
 * this component asks through its outputs. The age is never filled by it; the report only says what the file stated.
 * The token figure is an estimate, labelled as one, because the runtime's own count is not available to the browser.
 */
@Component({
  selector: 'lyr-card-import-preview',
  imports: [TranslatePipe],
  templateUrl: './card-import-preview.html',
  styleUrl: './card-import-preview.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CardImportPreview {
  readonly cardImport = input.required<CardImport>();
  readonly source = input.required<ImportSource>();
  /** The estimated tokens of the text in the form. */
  readonly tokens = input.required<number>();
  readonly createScenarios = input.required<boolean>();
  /** Dotted card paths of the fields read from the file, marked **dalla card**. */
  readonly cardFields = input<readonly string[]>([]);
  /** Dotted card paths of the fields the model wrote, marked **proposto**. */
  readonly proposed = input<readonly string[]>([]);
  /** True while the conversion is queued or running. */
  readonly converting = input(false);
  /** The Italian reason the last conversion did not finish, or an empty text. */
  readonly conversionFailure = input('');

  readonly sourceChange = output<ImportSource>();
  readonly convert = output();
  readonly createScenariosChange = output<boolean>();

  protected readonly longCard = computed(() => this.tokens() > LONG_CARD_TOKENS);
  protected readonly embedded = computed(() => this.cardImport().embedded_lyrveil);
  protected readonly scenarios = computed(() => this.cardImport().direct_draft?.scenarios ?? []);
  protected readonly cardLabels = computed(() => this.labels(this.cardFields()));
  protected readonly proposedLabels = computed(() => this.labels(this.proposed()));
  /** The free texts of the file edited outside Lyrveil (`description`, `personality`, `mes_example`), by label. */
  protected readonly changedLabels = computed(() =>
    (this.embedded()?.changed_fields ?? []).map((name) => CHANGED_TEXT[name] ?? 'card.changed.description'),
  );

  protected readonly ageText = computed<{ readonly key: TextKey; readonly value: number }>(() => {
    const age = this.cardImport().report?.age;
    switch (age?.status) {
      case 'stated':
        return { key: 'card.age.stated', value: age.value ?? 0 };
      case 'conflicting':
        return { key: 'card.age.conflicting', value: 0 };
      case 'not_adult':
        return { key: 'card.age.notAdult', value: 0 };
      default:
        return { key: 'card.age.notStated', value: 0 };
    }
  });

  protected macroAction(macro: MacroUse): TextKey {
    return MACRO_ACTION[macro.action] ?? 'card.macro.placeholder';
  }

  protected pick(source: ImportSource): void {
    this.sourceChange.emit(source);
  }

  protected toggleScenarios(event: Event): void {
    this.createScenariosChange.emit((event.target as HTMLInputElement).checked);
  }

  /** The labels of the card fields at `paths`, once each; a path with no label is left out. */
  private labels(paths: readonly string[]): readonly TextKey[] {
    const keys = paths.map((path) => fieldLabelKey(path)).filter((key): key is TextKey => key !== null);
    return [...new Set(keys)];
  }
}
