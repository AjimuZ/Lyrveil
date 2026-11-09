// ob-avatar: a square picture of a person or character, or their initials when there is no picture.
import { ChangeDetectionStrategy, Component, computed, input, linkedSignal } from '@angular/core';

export type AvatarSize = 32 | 40 | 64 | 128;

/** Up to two uppercase initials from the first letters of the first two words of `name`; empty for a blank name. */
export function initialsOf(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .filter((word) => word.length > 0)
    .slice(0, 2)
    .map((word) => [...word][0]?.toLocaleUpperCase('it') ?? '')
    .join('');
}

/**
 * Shows `src` when there is one and it loads; otherwise the initials of `name` on `--ob-elevated`. It never draws a
 * made-up picture. Without `alt` the avatar is decorative (the name is written next to it); with `alt` it is an
 * image with that name.
 */
@Component({
  selector: 'ob-avatar',
  template: `
    @if (showImage()) {
      <img class="image" [src]="src()" alt="" (error)="broken.set(true)" />
    } @else {
      <span class="initials" aria-hidden="true">{{ initials() }}</span>
    }
  `,
  styleUrl: './avatar.scss',
  host: {
    '[attr.data-size]': 'size()',
    '[attr.role]': "alt() ? 'img' : null",
    '[attr.aria-label]': 'alt() || null',
    '[attr.aria-hidden]': "alt() ? null : 'true'",
  },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObAvatar {
  readonly name = input.required<string>();
  readonly src = input<string | null>(null);
  readonly size = input<AvatarSize>(40);
  readonly alt = input('');

  /** True after the current `src` failed to load; a new `src` gets a new try. */
  protected readonly broken = linkedSignal<string | null, boolean>({ source: this.src, computation: () => false });
  protected readonly showImage = computed(() => !!this.src() && !this.broken());
  protected readonly initials = computed(() => initialsOf(this.name()));
}
