// ob-gauge: a segmented gauge of a real measured value, or a neutral track when the value is unknown.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Number of segments of the track. */
export const GAUGE_SEGMENTS = 10;

/**
 * How many of `segments` are filled for `value` out of `max`: the nearest whole number of the real proportion, clamped
 * to the track. Returns null when the value is unknown (null, not finite, negative) or `max` is not positive, so a
 * gauge never draws a measurement that does not exist.
 */
export function filledSegments(value: number | null, max: number, segments = GAUGE_SEGMENTS): number | null {
  if (value === null || !Number.isFinite(value) || value < 0 || !Number.isFinite(max) || max <= 0) {
    return null;
  }
  return Math.round(Math.min(value / max, 1) * segments);
}

/**
 * The gauge is an image whose accessible name is `label`, which states the real values ("VRAM: 6,2 di 12 GB"): the
 * segments only approximate them. A null `value` shows a striped track and the label should say it is unknown.
 */
@Component({
  selector: 'ob-gauge',
  template: `
    <div class="track" role="img" [attr.aria-label]="label()" [class.unknown]="filled() === null">
      @for (segment of segments; track segment) {
        <span class="segment" [class.on]="segment < lit()"></span>
      }
    </div>
  `,
  styleUrl: './gauge.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObGauge {
  readonly value = input.required<number | null>();
  readonly max = input(100);
  readonly label = input.required<string>();

  protected readonly segments = Array.from({ length: GAUGE_SEGMENTS }, (_, index) => index);
  protected readonly filled = computed(() => filledSegments(this.value(), this.max()));
  protected readonly lit = computed(() => this.filled() ?? 0);
}
