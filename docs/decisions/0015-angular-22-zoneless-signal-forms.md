# ADR 0015: Angular 22, zoneless, Signal Forms, Angular Aria

Status: accepted (2026-11-01). Supersedes: none.

## Context
The requirements ask for Angular 19 or a later compatible version, standalone components, signals, SCSS and zoneless when stable. On 2026-11-01, Angular 22.2.4 is `latest`, in active support until 2027-06. Zoneless has been the default since v21. Signal Forms are stable from v22. Angular 22 requires TypeScript `>=6.0 <6.1`, while npm's `latest` TypeScript is 7.0.2.

## Decision
- Angular 22.2 with TypeScript 6.0.3 pinned.
- Zoneless, standalone components, signals, client-side rendering only.
- Signal Forms for all forms. `@angular/aria` and `@angular/cdk` for accessible behavior, with no styled component library.
- A small signal-based `Translator` with JSON catalogs. File names follow the current style without role suffixes.
- API types generated with `@hey-api/openapi-ts`, because `openapi-typescript` 7.13.0 declares a peer range limited to TypeScript 5.

## Alternatives
- Angular 19: no longer supported.
- Angular Material or PrimeNG: their look conflicts with Obsidian Bloom.
- `@angular/localize`: one build per locale, for a single-language 1.0.
- Reactive Forms: mature, but not the recommendation for new v22 forms.
- SSR: no benefit for a local app, and it adds a server.

## Consequences
The frontend starts modern and within long support. Signal Forms are recent, so the Creator is the first place to watch for gaps. Reactive Forms remain available for a specific form if a blocking gap appears, recorded in a new ADR. TypeScript must not follow npm's `latest` until Angular allows it.
