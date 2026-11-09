// ob-skeleton: static stepped blocks that stand in for content while it loads.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/**
 * `lines` blocks whose widths step down (100 %, 75 %, 50 %) and repeat. They never animate. `label` says what is
 * loading and is read by assistive technology (`role="status"`), because the blocks alone say nothing.
 */
@Component({
  selector: 'ob-skeleton',
  template: `
    <div class="blocks" aria-hidden="true">
      @for (line of rows(); track line) {
        <span class="block"></span>
      }
    </div>
    <span class="ob-sr-only" role="status">{{ label() }}</span>
  `,
  styleUrl: './skeleton.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObSkeleton {
  readonly lines = input(3);
  readonly label = input.required<string>();

  protected readonly rows = computed(() => Array.from({ length: Math.max(1, Math.floor(this.lines())) }, (_, i) => i));
}
