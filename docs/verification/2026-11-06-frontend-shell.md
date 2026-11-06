# Frontend shell, 2026-11-06

Scope: roadmap item P01.5a, the `frontend/` workspace: the zoneless Angular shell, the lazy route table, the Obsidian Bloom tokens, the bundled fonts, the text catalog and its `Translator`, the lint and build gates with T-SEC-09, the unit tests, and the `Dockerfile` and `nginx.conf` ([Frontend](../frontend.md), [Design system](../design-system.md)).

Machine: Windows 11 Pro, Node.js 24.22.1 and the npm it ships. Commands ran from Git Bash in the repository root.

## Lock

| Command | Result |
| --- | --- |
| `npm install --prefix frontend --ignore-scripts --no-audit --no-fund` | Wrote `frontend/package-lock.json`, 386 packages. The direct dependencies are exact pins: Angular 22.2.4, CLI and `@angular/build` 22.2.5, TypeScript 6.0.3, RxJS 7.8.2, tslib 2.9.1, Vitest 5.0.5, jsdom 31.0.0, ESLint 10.14.0, `@eslint/js` 10.0.1, typescript-eslint 8.72.1, angular-eslint 22.6.0 and the three font packages at 5.3.0 |

`npm view` gave the peer ranges that fixed these choices: `@angular/build` 22.2.5 needs TypeScript `>=6.0 <6.1` and accepts Vitest `^4.0.8 || ^5.0.0`; typescript-eslint 8.72.1 accepts TypeScript `<6.1.0` and ESLint 10; angular-eslint 22.6.0 accepts ESLint 9 or 10. The lock lists the Linux packages that the `node:24.22.2-alpine` build stage needs, such as `@esbuild/linux-x64` and `@parcel/watcher-linux-x64-musl`.

## Results

| Command | Exit | Result |
| --- | --- | --- |
| `npm run lint --prefix frontend` | 0 | ESLint with `--max-warnings 0`: no finding. `tools/check-styles.mjs`: 0 color literals outside `src/styles/tokens.scss` |
| `npm run build --prefix frontend` | 0 | `ng build --configuration production` printed no warning. Initial files 233.42 kB raw, 63.99 kB estimated transfer; one lazy chunk, `unavailable-screen`, 1.08 kB. The output check found no network reference, and every font the CSS names is in `dist/lyrveil/browser/media`: 14 Inter, 12 JetBrains Mono and 6 Pixelify Sans files |
| `npm run test --prefix frontend` | 0 | `ng test --watch=false`: Vitest 5.0.5 in jsdom, 2 files, 7 tests passed. `node --test "tools/**/*.test.mjs"`: 7 tests passed |
| `scripts/lyrveil.cmd test lint`, last run after these documents | 0 | 81 Markdown files, 692 links and anchors, 0 broken. PSScriptAnalyzer finds nothing in 8 scripts, the backend and orchestrator gates exit 0, and the new frontend part runs `npm run lint` and `npm run build`, both exit 0 |

The Vitest tests cover the `Translator` (text, Italian plural forms through `Intl.PluralRules`, the `it-IT` count format with `1200` and `12.000`, parameters, and a non-empty text for every catalog entry) and the route table (exactly the 13 documented screens, each with `loadComponent` and a title key in the catalog; `/` and an unknown path both land on `/chats`; `/characters/abc/memory` renders its title and the not-available note).

T-SEC-09 is `frontend/tools/eslint/unsafe-html.test.mjs`. It lints the two fixtures in `frontend/tools/eslint/fixtures/` with the project's real ESLint configuration. In `unsafe-html.html`, the `[innerHTML]`, `[outerHTML]` and `bind-innerHTML` bindings are reported and the text interpolation is not. In `unsafe-html.ts`, six lines are reported: the `[innerHTML]` binding of an inline template, an `[innerHTML]` host binding, `@HostBinding('outerHTML')`, an `innerHTML` assignment, `insertAdjacentHTML` and `bypassSecurityTrustHtml`.

`frontend/tools/tools.test.mjs` checks what the two gates report: hex colors, color functions and named colors in declarations, but not tokens, `color-mix(`, comments or strings; build output lines with `WARNING`; and which references count as network loads.

## Not executed

| Item | Reason | Follow-up |
| --- | --- | --- |
| `docker build -f frontend/Dockerfile .` and a request to the running nginx | No image build or container run of the frontend was allowed in this session | P01.6, when Compose builds and starts `lyrveil-frontend` |
| A production build that prints a real warning, run through the gate | Only the warning parser was tested, on a sample line | Any later build warning fails the gate |
| The full `scripts/lyrveil.cmd test unit` through the command | The frontend tests ran through npm directly | Next full run of the level |
| Loading the app in a browser | No browser test exists yet | P01.5 follow-ups and the end-to-end level |
