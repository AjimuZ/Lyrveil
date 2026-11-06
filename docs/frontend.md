# Frontend

Status: accepted (2026-11-01). Implementation: partial. P01.5a delivered the zoneless shell, the route table with placeholder screens, the tokens, the bundled fonts, the text catalog with `Translator`, the lint and build gates, and the `Dockerfile` and `nginx.conf` ([evidence](verification/2026-11-06-frontend-shell.md)). API types, the WebSocket client and every real screen are planned, phases P01 to P08.

Angular 22 with standalone components, signals, zoneless change detection, SCSS and client-side rendering. The style is [Obsidian Bloom](design-system.md). See
[ADR 0015](decisions/0015-angular-22-zoneless-signal-forms.md).

## Structure

File names follow the Angular v20+ style without role suffixes. Interfaces keep `.model.ts`.

```text
frontend/
  angular.json  package.json  package-lock.json  tsconfig*.json  eslint.config.mjs  Dockerfile  nginx.conf
  tools/build-icons.mjs       Planned
  tools/build-strict.mjs      Production build that fails on a warning or a network reference
  tools/check-styles.mjs      Fails on a color literal outside styles/tokens.scss
  tools/eslint/               T-SEC-09 restrictions, their fixtures and test
  src/
    assets/icons/             ASCII icon grids
    i18n/it.json              Interface text
    styles/                   tokens.scss, reset.scss, typography.scss, global.scss
    app/
      app.ts  app.routes.ts  app.config.ts
      core/
        api/                  Generated types, HTTP providers, error mapping
        realtime/             events-socket.ts: WebSocket, reconnect, replay
        i18n/                 translator.ts, translate-pipe.ts
        session/              first-start notice, display name
        shell/                Layout, navigation, breakpoints; unavailable-screen.ts until each screen exists
      shared/
        ui/                   ob-* primitives
        pipes/                relative-time.ts, day-label.ts
      features/
        chats/                List, search, conversation store
        conversation/         Messages, composer, variants, lightbox, context panel
        characters/           Creator, profile, versions, avatar tools
        memory-studio/        Memories, events, relationship, summaries, diagnostics
        gallery/
        scenarios/
        settings/             General, models, resources, data
```

## Routes

All feature routes load lazily.

| Path | Screen |
| --- | --- |
| `/` | Redirects to `/chats` |
| `/chats` | 1. Conversation list. On desktop the list is always visible |
| `/chats/:conversationId` | 2. Conversation |
| `/characters` | Character list |
| `/characters/new` | 3. Character Creator |
| `/characters/:id` | 4. Character Profile |
| `/characters/:id/edit` | 3. Character Creator |
| `/characters/:id/memory` | 5. Character Memory Studio. Query `conversation` filters by chat |
| `/gallery` | 6. Image gallery |
| `/scenarios` | 7. Scenario management |
| `/settings` | 8. General settings |
| `/settings/models` | 9. Model settings |
| `/settings/resources` | 10. AI resource monitoring |
| `/settings/data` | Backup, export, deleted conversations |

Technical screens are reached only from `/settings`. Any other path redirects to `/chats`.

Until a phase delivers a screen, its route loads `core/shell/unavailable-screen.ts`, which shows the screen's title from
the route's `data.screen` key and says that the screen is not available yet. Delivering a screen replaces only the
route's `loadComponent`.

## State

| Rule | Detail |
| --- | --- |
| Signals first | Stores expose read-only signals and intent methods. Templates read signals directly. |
| Server state | `httpResource` for simple reads. Feature stores for conversation lists and message pages, which also merge events. |
| Events | `events-socket.ts` is the only WebSocket owner. Stores subscribe by event type. |
| RxJS | Only at the HTTP and WebSocket boundaries. |
| Effects | Only for DOM work and external sync, not for derived state. Derived state uses `computed` or `linkedSignal`. |
| Forms | Signal Forms for the Creator, scenarios, settings and memory editing. |
| Persistence | `localStorage` only for convenience: context panel state, last settings tab, unsent composer text per conversation, and the Creator draft. All reads and writes tolerate failure. |

The server is the source of truth. A store applies events optimistically and reloads through REST after `resync.required`.

### Realtime handling

1. Connect to `/ws`, then send `hello` with `last_event_id` and `boot_id`.
2. Reconnect with backoff of 1, 2, 5 and 10 seconds.
3. While disconnected, the shell shows "Lyrveil non risponde. Sto riprovando…". Sending stays available only after a REST check succeeds.
4. `message.delta` writes to one signal per streaming message, batched per animation frame, so long replies do not render per token.
5. `message.restarted` clears the streamed text of that message and keeps the typing indicator, so a corrected reply never shows the discarded words.

### Sending

1. Generate `client_message_id` and show the message at once as pending.
2. `POST` it. Retrying with the same identifier is safe.
3. On `202`, replace it with the server's message and show the reply placeholder.
4. A failure keeps the text with a retry action. Nothing typed is ever lost.

## Screens

Every screen has loading, empty and error states. Every control has real behavior.

### 1. Conversation list

Avatar, name, last message (one line), time, unread count, typing indicator, and a pending-image or queue hint. Search covers names, titles and messages. Pinned chats come first, then activity. A context menu offers pin, archive, delete. **+** opens
a picker: choose a character, an optional scenario and a language. The list has a separate tab for archived conversations.

### 2. Conversation

- Header: avatar, name, status (typing, queued, drawing), search, context panel toggle, menu.
- Messages: virtual scrolling with `@angular/cdk`, upward paging, day separators in Italian such as `sabato 1 novembre`, and timestamps. User messages show a simulated read state: one check `sent`, two checks `read`.
- Bubble actions, on hover or long press: edit, regenerate (latest reply), alternatives `< 2/3 >` (latest reply), delete, "Ricorda questo", copy.
- Images: thumbnail in the bubble. A lightbox shows the original full screen, with metadata in a collapsible section and regenerate. Arrow keys move between images.
- Placeholders: typing indicator, queued state, image progress with phase text and cancel.
- Composer: multi-line text, Enter sends, Shift+Enter adds a line, image button. The image form has request, aspect (**Automatico** by default, then square, portrait, landscape, tall and wide), outfit (**Lascia decidere alla scena**, **Abituale** or a named outfit), **Alta** or **Rapida**, and optional seed, steps and CFG. Character counter near the 8000 limit.
- Search: a bar with next and previous match, using `GET /conversations/{id}/search`.
- Editing an older message warns: "I ricordi collegati verranno rivalutati."
- Context panel: profile summary, relationship in words, recent events, images, scenario, proactive settings and a link to the Memory Studio. Diagnostic mode adds a link per reply, and only when it is on.

### 3. Character Creator

Tabs **Identità**, **Personalità**, **Comunicazione**, **Aspetto**. An assist panel at the top takes a natural-language description and shows queue state. A draft fills all the fields and marks them "proposto". Inline validation covers the required fields, including the age rule. Import and export actions are in the header. The primary action is **Salva personaggio**, and leaving with unsaved changes asks for confirmation.

- **Identità** adds **Come parla di sé**: **Al femminile**, **Al maschile** or **Non indicato**.
- **Comunicazione** adds *tu* or *Lei*, **Narrazione** (**Nessuna**, **Leggera**, **Libera**) and **Emoji**, each with a one-line
  example of its effect. Examples are edited as pairs: **Messaggio di chi scrive**, optional, and **Risposta del personaggio**.
  A counter such as **3 esempi su 5 entrano nel prompt** uses the active profile's tokenizer, or 4 characters per token
  while the text runtime is off.
  **Da evitare** takes up to 8 phrases.
- **Aspetto** handles upload, generate, regenerate, crop, variants and primary avatar. It also edits the identity slots:
  closed choices for hair color, length, eyes and skin, vocabulary chips with Italian search for the other slots, and free
  extra tags. Below them, the usual outfit and up to 5 named outfits. **Prompt persistente** shows the rendered English
  identity, read-only, with a warning when it would not fit in the first prompt chunk. Lock icons sit next to each field.

### 4. Character Profile

Avatar and variants, identity summary, personality, communication examples, conversation list, **Nuova conversazione**, **Modifica**, **Memoria**, version history with compare and restore, proposals for the profile, duplicate, archive and purge. A clear note states that the character
is simulated.

### 5. Character Memory Studio

Tabs:

| Tab | Content |
| --- | --- |
| **Ricordi** | Categories, search, filters (conversation, kind, provenance, status, locked). Each card shows content, a provenance pill, importance, date and source messages with a link. Actions: edit, delete, lock, confirm, share, history. |
| **Conflitti** | Conflict groups with sources side by side. Actions: choose the true one, or keep uncertain. |
| **Eventi** | Episode timeline with links between episodes. |
| **Rapporto** | Narrative description first, then change history with reasons. Numbers are in an advanced section with an explicit label. |
| **Riassunti** | Checkpoints with ranges, status and version. |
| **Proposte** | `identity_candidate` memories, with "Applica al profilo" and "Ignora". |
| **Diagnostica** | Visible only when diagnostic mode is on. For a chosen reply it shows blocks, tokens, memories with score parts, and summaries. |

### 6. Gallery

Grid of thumbnails with virtual scrolling. Filters by character, conversation, purpose and date. The lightbox has metadata (prompt, scene, outfit, aspect, seed, steps, CFG, model, workflow), regenerate with advanced options, a link to the source message, delete and save-as-avatar. The advanced options refuse `embedding:` and weights outside 0.5 to 1.5 with an Italian explanation.

### 7. Scenarios

List with search, edit form (title, description, setting, opening message, optional character, language) and archive. **Inizia conversazione** opens the new chat picker. A note says that existing conversations keep their copy.

### 8. General settings

Display name, **Come rivolgersi a te** (**Al femminile**, **Al maschile** or **Non indicato**), time zone view, interface language (`it` in 1.0), default language for new characters, first-start notice, global proactive switch, image auto-detect, contextual images, diagnostic mode, and links to the other sections.

### 9. Model settings

Two lists, **Modello di testo** and **Modello per immagini**, show every [profile](models.md) with its short purpose,
size, license, tier and state: **Verificato**, **Dimensione errata** or **Da scaricare**. A text profile that passed the
quality protocols also shows **Qualità verificata**, which links to a short summary of its results. A missing profile shows the
exact download command with a copy button. Profiles above the machine's tier are listed but disabled, with the memory
they need. Choosing a text profile shows **Applica ora**, which reloads the model after the running reply.

Below the lists: **Qualità immagini** (**Alta** or **Rapida**), and **Aggiungi file locale** for a custom profile,
marked **non verificato**.

Advanced values, per active profile: context, GPU layers, batch, experts in RAM (`MOE_CPU_LAYERS`, for
mixture-of-experts profiles only), temperature and other sampler values, reply length, DRY. Image: default steps, CFG,
sampler, scheduler, CLIP skip, second-pass scale, `denoise` and steps, and seed policy default. Each field shows its
default, and **Ripristina predefiniti** restores them. A note marks values applied at the next load.

### 10. AI resources

State in words and code, VRAM gauge, memory and disk gauges, queue by class, job list with cancel and retry, tokens per second, recent transitions and preflight results. Values that are unavailable show as unknown. The screen has a "Riprova" action during `ERROR`.

## Keyboard

| Keys | Action |
| --- | --- |
| `Ctrl+K` | Search conversations |
| `Ctrl+N` | New conversation |
| `Alt+↑`, `Alt+↓` | Previous, next conversation |
| `Ctrl+F` inside a chat | Search in the chat |
| `Enter`, `Shift+Enter` | Send, new line |
| `↑` in an empty composer | Edit the last user message |
| `Esc` | Close a dialog or panel, leave search |
| `←`, `→` in the lightbox | Previous, next image |

A help dialog under `?` lists them. Browser shortcuts stay intact when focus is outside the chat.

## Text and translation

`Translator` is a small signal-based service. It bundles `i18n/it.json` through a JSON import, so the text works offline and needs no request. The compiler derives the key types `TextKey` and `PluralKey` from the catalog, so an unknown key fails the build, and the `t` pipe checks keys in templates the same way. Keys are flat, such as `nav.settings`. An entry is a string or an object of plural forms by `Intl.PluralRules` category, with `other` required. `{name}` placeholders take parameters, numbers are formatted for the locale, and `{count}` receives the count of a plural. Dates and numbers use `Intl` with locale `it-IT`. All interface strings are in the catalog. Adding a language means adding a catalog.

Conversation language is a separate setting and affects only the characters' text.

## Security in the browser

- Messages render as text through Angular bindings. `innerHTML` and `bypassSecurityTrust*` are forbidden, and a lint rule enforces it: `tools/eslint/unsafe-html.mjs` configures ESLint's `no-restricted-syntax` to report `[innerHTML]` and `[outerHTML]` bindings in templates and in host metadata, `innerHTML`, `outerHTML` and `insertAdjacentHTML` in code, and any `bypassSecurityTrust*` identifier (T-SEC-09).
- `*emphasis*` is parsed into a node list. It produces `<em>`, with no HTML passing through.
- All external links are absent by design. The app makes no third-party request.
- Fonts, icons and scripts are same-origin. See the CSP in [Security](security.md#http-headers).

## Container

Multi-stage `frontend/Dockerfile`:

1. `node:24.22.2-alpine`: `npm ci --ignore-scripts`, then `node tools/build-strict.mjs`, the same strict build as the lint level.
2. `nginxinc/nginx-unprivileged:1.30.6-alpine`: copies `dist/lyrveil/browser` and `nginx.conf`, as `conf.d/default.conf`, port 8080.

`nginx.conf` serves `index.html` for client routes, caches hashed files for a year, proxies `/api`, `/media` and `/ws` to `lyrveil-backend:8000`, passes the original `Host` and `Origin`, disables proxy buffering on `/ws`, limits bodies to 80 MB, answers `GET /healthz` for the Compose health check, and sets the security headers. The image has not been built yet: see the [evidence](verification/2026-11-06-frontend-shell.md#not-executed).

## Tests

- Unit and component: Vitest through `ng test`, with `TestBed` and component harnesses, in jsdom. `npm run test` also runs the Node tests of `tools/`, T-SEC-09 among them. `scripts\lyrveil.cmd test unit` runs both.
- End-to-end: Playwright against the fake runtimes. See [Testing](testing.md).
- Accessibility: `@axe-core/playwright` on all ten screens.
- `ng build` must pass with no warnings before any commit that touches `frontend/`. `scripts\lyrveil.cmd test lint` runs `npm run lint` (ESLint with no warnings allowed, then the color check) and `npm run build` (`tools/build-strict.mjs`). The build fails on any line with `WARNING`, on a `url()`, `@import`, `src` or `href` that names another origin, and when a font the CSS names is missing from the output.
