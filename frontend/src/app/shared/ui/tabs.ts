// ob-tabs: a row of tabs with the keyboard behavior of the WAI-ARIA tabs pattern.
import { ChangeDetectionStrategy, Component, ElementRef, computed, input, model, viewChildren } from '@angular/core';

export interface TabItem {
  /** Stable identifier, the value of `selected`. */
  readonly id: string;
  readonly label: string;
}

let nextTabsId = 0;

/** The tab to select after `key` from `index` among `count` tabs, or null when the key does not move the selection. */
export function tabAfterKey(key: string, index: number, count: number): number | null {
  switch (key) {
    case 'ArrowRight':
      return (index + 1) % count;
    case 'ArrowLeft':
      return (index - 1 + count) % count;
    case 'Home':
      return 0;
    case 'End':
      return count - 1;
    default:
      return null;
  }
}

/**
 * The tab list and its panel. `selected` is the id of the active tab (two-way). Only the active tab is in the Tab
 * order; the arrow keys, Home and End move to a tab and select it. The projected content is the panel of the active
 * tab, so the parent shows the content that matches `selected`. The active tab has the pixel cursor.
 */
@Component({
  selector: 'ob-tabs',
  template: `
    <div class="list" role="tablist" [attr.aria-label]="label()">
      @for (tab of tabs(); track tab.id; let index = $index) {
        <button
          #tabButton
          type="button"
          role="tab"
          class="tab"
          [attr.id]="tabId(tab.id)"
          [attr.aria-selected]="tab.id === activeId()"
          [attr.aria-controls]="panelId"
          [attr.tabindex]="tab.id === activeId() ? 0 : -1"
          (click)="select(tab.id)"
          (keydown)="onKeydown($event, index)"
        >
          <span class="cursor" aria-hidden="true"></span>
          {{ tab.label }}
        </button>
      }
    </div>
    <div class="panel" role="tabpanel" [attr.id]="panelId" [attr.aria-labelledby]="tabId(activeId())">
      <ng-content />
    </div>
  `,
  styleUrl: './tabs.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObTabs {
  readonly tabs = input.required<readonly TabItem[]>();
  /** The accessible name of the tab list. */
  readonly label = input.required<string>();
  readonly selected = model('');

  private readonly buttons = viewChildren<ElementRef<HTMLButtonElement>>('tabButton');
  private readonly uid = nextTabsId++;
  protected readonly panelId = `ob-tabs-${this.uid}-panel`;
  /** The selected tab, or the first one while `selected` names none, so the list is always reachable by Tab. */
  protected readonly activeId = computed(() => {
    const tabs = this.tabs();
    return tabs.some((tab) => tab.id === this.selected()) ? this.selected() : (tabs[0]?.id ?? '');
  });

  protected tabId(id: string): string {
    return `ob-tabs-${this.uid}-tab-${id}`;
  }

  protected select(id: string): void {
    this.selected.set(id);
  }

  protected onKeydown(event: KeyboardEvent, index: number): void {
    const target = tabAfterKey(event.key, index, this.tabs().length);
    const tab = target === null ? undefined : this.tabs()[target];
    if (tab === undefined) {
      return;
    }
    event.preventDefault();
    this.selected.set(tab.id);
    this.buttons()[target ?? 0]?.nativeElement.focus();
  }
}
