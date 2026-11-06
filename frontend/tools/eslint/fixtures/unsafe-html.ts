// Fixture for T-SEC-09: every construct below except the text binding must be reported by the lint rule.
import { Component, HostBinding, inject } from '@angular/core';
import { DomSanitizer } from '@angular/platform-browser';

@Component({
  selector: 'lyr-unsafe-html',
  template: '<p>{{ text }}</p><div [innerHTML]="text"></div>',
  host: { '[innerHTML]': 'text' },
})
export class UnsafeHtml {
  @HostBinding('outerHTML') readonly html = '';
  readonly text = '<em>x</em>';
  private readonly sanitizer = inject(DomSanitizer);

  render(element: HTMLElement): unknown {
    element.innerHTML = this.text;
    element.insertAdjacentHTML('beforeend', this.text);
    return this.sanitizer.bypassSecurityTrustHtml(this.text);
  }
}
