// Template access to the Translator: `{{ 'nav.chats' | t }}`, with the key checked by strict templates.
import { Pipe, PipeTransform, inject } from '@angular/core';

import { TextKey, TextParams, Translator } from './translator';

// Impure so that the template re-reads the Translator's signals; the lookup is a property read and a replace.
@Pipe({ name: 't', pure: false })
export class TranslatePipe implements PipeTransform {
  private readonly translator = inject(Translator);

  /** Returns the catalog text for key with params filled in, as Translator.t does. */
  transform(key: TextKey, params?: TextParams): string {
    return this.translator.t(key, params);
  }
}
