// Resolves interface text from the bundled catalog, with typed keys, placeholders and Intl plural rules.
import { Injectable, computed, signal } from '@angular/core';

import italian from '../../../i18n/it.json';

/** The shape of every catalog: the Italian one is the reference, so another language must provide the same keys. */
export type Catalog = typeof italian;

/** Plural forms of one entry, by Intl plural category. `other` is required because every locale has it. */
export type PluralForms = { readonly other: string } & Partial<Readonly<Record<Intl.LDMLPluralRule, string>>>;

/** Keys of plain text entries. The compiler derives them from the catalog, so an unknown key does not build. */
export type TextKey = { [K in keyof Catalog]: Catalog[K] extends string ? K : never }[keyof Catalog];

/** Keys of entries with plural forms. */
export type PluralKey = { [K in keyof Catalog]: Catalog[K] extends PluralForms ? K : never }[keyof Catalog];

/** Values for `{name}` placeholders. Numbers are formatted for the interface locale. */
export type TextParams = Readonly<Record<string, string | number>>;

type CatalogEntries = Readonly<Record<string, string | PluralForms>>;

const PLACEHOLDER = /\{(\w+)\}/g;

@Injectable({ providedIn: 'root' })
export class Translator {
  // Signals so that templates reading text re-render if the catalog or locale ever changes; 1.0 ships only Italian.
  private readonly entries = signal<CatalogEntries>(italian);
  readonly locale = signal('it-IT');

  private readonly pluralRules = computed(() => new Intl.PluralRules(this.locale()));
  private readonly numbers = computed(() => new Intl.NumberFormat(this.locale()));

  /**
   * Returns the text for key with its placeholders filled from params. A placeholder without a value stays as
   * written, so a missing parameter shows instead of vanishing silently. Never throws for a typed key.
   */
  t(key: TextKey, params: TextParams = {}): string {
    const entry = this.entries()[key];
    return this.fill(typeof entry === 'string' ? entry : key, params);
  }

  /**
   * Returns the plural form of key that Intl.PluralRules selects for count in the current locale, falling back to
   * `other` when the catalog lacks that category. `{count}` receives the formatted count, unless params sets it.
   */
  plural(key: PluralKey, count: number, params: TextParams = {}): string {
    const entry = this.entries()[key];
    if (entry === undefined || typeof entry === 'string') {
      return this.fill(key, params);
    }
    const form = entry[this.pluralRules().select(count)] ?? entry.other;
    return this.fill(form, { count, ...params });
  }

  private fill(template: string, params: TextParams): string {
    return template.replace(PLACEHOLDER, (placeholder, name: string) => {
      const value = params[name];
      if (value === undefined) {
        return placeholder;
      }
      return typeof value === 'number' ? this.numbers().format(value) : value;
    });
  }
}
