// ob-bubble: one message as plain text with line breaks and *emphasis*, never as HTML.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** A run of a line: `italic` when it was written between asterisks. */
export interface BubbleSegment {
  readonly text: string;
  readonly italic: boolean;
}

// An opening asterisk is not followed by space or another asterisk, a closing one is not preceded by them, and
// neither touches a word character on the outside, so "2 * 3 * 4" and "a*b*c" stay as written.
const EMPHASIS = /(?<![*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/g;

/**
 * Splits a message into lines, and each line into runs. `*word*` becomes an italic run without its asterisks; any
 * other character, including angle brackets, stays text. The result is rendered through interpolation, which
 * escapes it, so a message cannot add markup. An empty line is an empty array.
 */
export function parseBubble(text: string): BubbleSegment[][] {
  return text.split(/\r\n|\r|\n/).map((line) => {
    const segments: BubbleSegment[] = [];
    let last = 0;
    for (const match of line.matchAll(EMPHASIS)) {
      if (match.index > last) {
        segments.push({ text: line.slice(last, match.index), italic: false });
      }
      segments.push({ text: match[1] ?? '', italic: true });
      last = match.index + match[0].length;
    }
    if (last < line.length) {
      segments.push({ text: line.slice(last), italic: false });
    }
    return segments;
  });
}

/**
 * The character's bubble is `--ob-surface`, the user's `--ob-bubble-user`. Alignment and the maximum width belong to
 * the conversation that places the bubbles.
 */
@Component({
  selector: 'ob-bubble',
  template: `
    @for (line of lines(); track $index) {
      <p class="line">
        @for (segment of line; track $index) {
          @if (segment.italic) {
            <em>{{ segment.text }}</em>
          } @else {
            <span>{{ segment.text }}</span>
          }
        }
      </p>
    }
  `,
  styleUrl: './bubble.scss',
  host: { '[class.user]': "author() === 'user'" },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObBubble {
  readonly author = input.required<'character' | 'user'>();
  readonly text = input.required<string>();

  protected readonly lines = computed(() => parseBubble(this.text()));
}
