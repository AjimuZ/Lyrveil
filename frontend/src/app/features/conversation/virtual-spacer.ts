// An empty, hidden list item of a given height: it stands in for the rows a virtual list does not draw.
import { Directive, input } from '@angular/core';

/**
 * Sets the height of the element it is on, in pixels. The height is a style set through the DOM rather than a markup
 * attribute, which the Content-Security-Policy allows and the template lint (no inline styles) leaves to a directive.
 */
@Directive({
  selector: 'li[lyrSpace]',
  host: { 'aria-hidden': 'true', class: 'space', '[style.height.px]': 'height()' },
})
export class VirtualSpacer {
  /** The height in pixels. */
  readonly height = input.required<number>({ alias: 'lyrSpace' });
}
