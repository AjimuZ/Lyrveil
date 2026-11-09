// Shortcut help: the dialog under `?` that lists every keyboard shortcut of docs/frontend.md.
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey } from '../../core/i18n/translator';
import { ObDialog } from '../../shared/ui/dialog';
import { ShortcutService } from './shortcut-service';

interface Row {
  readonly keys: readonly string[];
  readonly action: TextKey;
}

/** The table of docs/frontend.md (Keyboard), one row per action. Key names are not translated. */
export const SHORTCUT_ROWS: readonly Row[] = [
  { keys: ['Ctrl+K'], action: 'shortcuts.search' },
  { keys: ['Ctrl+N'], action: 'shortcuts.newChat' },
  { keys: ['Alt+↑', 'Alt+↓'], action: 'shortcuts.stepChat' },
  { keys: ['Ctrl+F'], action: 'shortcuts.find' },
  { keys: ['Invio', 'Maiusc+Invio'], action: 'shortcuts.send' },
  { keys: ['↑'], action: 'shortcuts.editLast' },
  { keys: ['Esc'], action: 'shortcuts.close' },
  { keys: ['←', '→'], action: 'shortcuts.lightbox' },
  { keys: ['?'], action: 'shortcuts.help' },
];

/** The help dialog. It opens from `ShortcutService.helpOpen` and returns focus to the control that had it. */
@Component({
  selector: 'lyr-shortcut-help',
  imports: [TranslatePipe, ObDialog],
  template: `
    <ob-dialog [label]="'shortcuts.title' | t" [(open)]="service.helpOpen">
      <h2 class="title">{{ 'shortcuts.title' | t }}</h2>
      <dl class="list">
        @for (row of rows; track row.action) {
          <div class="row">
            <dt>
              @for (keys of row.keys; track keys; let last = $last) {
                <kbd class="key">{{ keys }}</kbd>
                @if (!last) {
                  <span class="or">{{ 'shortcuts.or' | t }}</span>
                }
              }
            </dt>
            <dd>{{ row.action | t }}</dd>
          </div>
        }
      </dl>
      <p class="note">{{ 'shortcuts.mac' | t }}</p>
      <button type="button" class="close" (click)="service.helpOpen.set(false)">{{ 'shortcuts.closeButton' | t }}</button>
    </ob-dialog>
  `,
  styleUrl: './shortcut-help.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ShortcutHelp {
  protected readonly service = inject(ShortcutService);
  protected readonly rows = SHORTCUT_ROWS;
}
