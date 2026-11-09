# Design system: Obsidian Bloom

Status: accepted (2026-11-01). Implementation: partial. The color and non-color tokens, the three bundled font families and the color literal check exist since P01.5a ([evidence](verification/2026-11-06-frontend-shell.md)). The icons, the icon build and the `ob-*` primitives exist since P08.1a; their Angular specs (`shared/ui/*-primitives.spec.ts`) were not run in that task, so they are written but not yet shown to pass; the icon build tests (`tools/build-icons.test.mjs`) and the lint level did pass. Reconciled on 2026-11-09: the motion rules and the loading and empty states of four screens (P08.1b), the responsive layouts (P08.2) and the keyboard, focus and live-region rules (P08.3) are delivered, as the implementation notes below say. Their end-to-end specs (`empty-loading`, `responsive`, `accessibility`) and the Angular specs of the primitives are written and have not run, so T-QLT-03 is not shown to pass. The motion of the remaining screens and the P08 exit checks are not delivered.

Obsidian Bloom is 80 % modern messaging app and 20 % quiet JRPG identity. The result must feel like a real product used every day. Readability always beats retro
fidelity. It never looks like a dashboard, a terminal or a game menu.

## Principles

| Do | Avoid |
| --- | --- |
| Dark surfaces, layered by contrast | Excessive neon or glow |
| Thin frames and slightly rounded corners | Invasive glassmorphism |
| Small stepped corner marks on main windows | Bulky pixel decoration or sprites |
| A small pixel cursor on the selected item | Arcade effects, scanlines, dithered text |
| Brief, discreet motion in few steps | Continuous animation, except real activity indicators |
| Real values in gauges | Invented numbers or percentages |

## Color tokens

These are the required brand tokens. They never change.

| Token | CSS variable | Value | Use |
| --- | --- | --- | --- |
| Background | `--ob-bg` | `#100D15` | Page |
| Surface | `--ob-surface` | `#211827` | Panels, character bubbles |
| Elevated surface | `--ob-elevated` | `#2A2032` | Menus, dialogs, hover layers |
| Border | `--ob-border` | `#493047` | Decorative frames and separators |
| Primary | `--ob-primary` | `#C77DAD` | Primary actions, active items |
| Primary hover | `--ob-primary-hover` | `#DEA1C5` | Hover on primary, focus rings |
| Secondary accent | `--ob-accent` | `#8D70B0` | Control borders, secondary highlights |
| Primary text | `--ob-text` | `#F4D8E9` | Main text |
| Secondary text | `--ob-text-muted` | `#A99AAB` | Timestamps, secondary text |

### Semantic extension

The brand palette has no error, warning or success hue. Destructive actions need a distinct color, so three tokens extend it
([ADR 0023](decisions/0023-obsidian-bloom-accessibility-extensions.md)). Status always uses words as well as color.

| CSS variable | Value | Use |
| --- | --- | --- |
| `--ob-danger` | `#E5737F` | Errors, destructive actions |
| `--ob-warning` | `#E0B070` | Warnings, degraded states |
| `--ob-success` | `#7FBF9A` | Confirmations |

### Derived tokens

Other shades derive from the tokens. No other literal color appears in the code.

```scss
:root {
  --ob-control-border: var(--ob-accent);
  --ob-focus: var(--ob-primary-hover);
  --ob-bubble-user: color-mix(in srgb, var(--ob-primary) 18%, var(--ob-surface)); /* #3F2A3F */
  --ob-selected: color-mix(in srgb, var(--ob-primary) 12%, var(--ob-surface));    /* #352437 */
  --ob-hover: color-mix(in srgb, var(--ob-text-muted) 10%, var(--ob-surface));    /* #2F2534 */
}
```

`scripts\lyrveil.cmd test lint` fails when a color literal appears outside the token file, `frontend/src/styles/tokens.scss`. The check, `frontend/tools/check-styles.mjs`, finds hex colors, color functions such as `rgb(` and `oklch(`, and CSS named colors in declaration values, in every `.scss` and `.css` file under `frontend/src/`. `color-mix(` of tokens, `transparent` and `currentColor` are allowed. ESLint forbids inline component `styles` and template style attributes, so every component style is in a file the check reads.

### Contrast

Computed with the WCAG 2.2 formula on 2026-11-01. Text needs 4.5:1. Controls and focus rings need 3:1.

| Pair | Ratio | Rule |
| --- | --- | --- |
| Text on background, surface, elevated | 14.53, 12.94, 11.73 | Any text |
| Muted on background, surface, elevated | 7.24, 6.45, 5.85 | Any text |
| Text on user bubble, selected, hover | 9.82, 10.86, 11.04 | Any text |
| Muted on user bubble, selected, hover | 4.89, 5.41, 5.50 | Any text |
| Primary on background, surface, elevated | 6.39, 5.69, 5.16 | Text and icons |
| Background on primary fill | 6.39 | **Text on primary buttons** |
| Background on primary hover fill | 9.18 | Text on hover |
| Accent on background | 4.66 | Text allowed |
| Accent on surface, elevated | 4.15, 3.76 | Controls and large text only |
| Primary text on accent fill | 3.12 | **Forbidden for small text.** Use the background color |
| Background on accent fill | 4.66 | Text on accent fills |
| Border on background, surface, elevated | 1.65, 1.47, 1.33 | **Decorative only.** Never the only boundary of a control |
| Focus ring on background, surface, elevated | 9.18, 8.17, 7.41 | Focus |
| Danger on background, surface, elevated | 6.50, 5.79, 5.25 | Text. Use on these three only: 4.39 on the user bubble is too low |
| Warning on background, surface, elevated | 9.73, 8.67, 7.86 | Text |
| Success on background, surface, elevated | 9.01, 8.02, 7.27 | Text |

The brand border cannot identify an input by itself, so fields use `--ob-control-border`.

## Non-color tokens

```scss
:root {
  --ob-px: 2px;              /* Pixel unit for ornaments */
  --ob-radius-sm: 2px;       /* Controls, pills */
  --ob-radius-md: 4px;       /* Windows, cards */
  --ob-radius-bubble: 6px;   /* Message bubbles */
  --ob-space-1: 4px;  --ob-space-2: 8px;  --ob-space-3: 12px;
  --ob-space-4: 16px; --ob-space-5: 24px; --ob-space-6: 32px;
  --ob-quick: 120ms;         /* Hover and focus */
  --ob-panel: 160ms;         /* Panels and dialogs */
  --ob-shadow-overlay: 0 4px 16px color-mix(in srgb, var(--ob-bg) 70%, transparent);
  --ob-font-ui: "Inter Variable", system-ui, "Segoe UI", sans-serif;
  --ob-font-pixel: "Pixelify Sans", var(--ob-font-ui);
  --ob-font-mono: "JetBrains Mono", ui-monospace, Consolas, monospace;
}
```

Depth comes from frames and layers. The one shadow belongs to floating overlays such as menus and dialogs.

## Typography

| Role | Font | Size and weight |
| --- | --- | --- |
| Message text | Inter | 15 px, 400, line height 1.5 |
| Interface text | Inter | 14 px, 400 and 500 |
| Secondary text | Inter | 12 px, 400, muted color |
| Page and window titles | Pixelify Sans | 18 to 24 px, 500 |
| Brand mark | Pixelify Sans | 24 px and up |
| Short graphic labels, up to 2 words | Pixelify Sans | At least 14 px |
| Identifiers, seeds, diagnostics | JetBrains Mono | 12 px |

Pixel fonts never appear in messages, paragraphs, form labels or small badges. All three families are bundled locally through `@fontsource`, so the app works offline: Inter Variable upright and italic, Pixelify Sans 500 and JetBrains Mono 400, every subset of each, listed in the `styles` of `frontend/angular.json`. P08 checks Pixelify Sans
with `àèéìòù`, and records file hashes in the license notices.

## Icons

- Original pixel icons on a 16 x 16 grid. 24 x 24 for empty states only.
- Source: ASCII grids in `frontend/src/assets/icons/*.txt`, converted to one SVG sprite by `frontend/tools/build-icons.mjs`.
- Monochrome with `fill: currentColor` and `shape-rendering: crispEdges`.
- Decorative icons have `aria-hidden="true"`. Icon-only buttons have an accessible name.
- Required set: `chat`, `plus`, `search`, `pin`, `archive`, `trash`, `pencil`, `refresh`, `image`, `send`, `check`, `check-double`, `arrow-left`, `arrow-right`, `chevron`, `close`, `gear`, `user`, `brain`, `lock`,
  `unlock`, `bell`, `star`, `upload`, `download`, `warning`, `error`, `gpu`, `scene`, `clock`.
- A grid is 16 lines of 16 characters, `#` for a filled pixel and `.` for an empty one. A name that starts with `empty-` is a 24 x 24 grid for an empty state (`empty-chat` and `empty-image` exist). The builder rejects any other size, a character other than `#` and `.`, an empty grid and a missing required icon.
- `node tools/build-icons.mjs build` (in `frontend/`) writes `src/assets/icons/sprite.svg` and `src/app/shared/ui/icon-names.ts`; `check` writes nothing. The output depends on the grids only, so a second build is identical. `npm run lint` runs the check and fails when a built file differs from the grids. `angular.json` copies the sprite to `/assets/icons/sprite.svg`.
- `ob-icon` (`name`, optional `label`, whole-number `scale`) draws one symbol of the sprite and is decorative unless it has a `label`.

## Primitives

All are standalone components in `frontend/src/app/shared/ui/`, with the prefix `ob-`.

| Component | Rules |
| --- | --- |
| `ob-window` | Surface, 1 px frame, `--ob-radius-md`. Primary windows have small stepped corner marks made from `--ob-px` squares in `--ob-accent`. Plain cards have no marks. |
| `ob-menu-item` | Used in menus and the chat list. Selected: `--ob-selected`, frame, stepped pixel cursor drawn with `clip-path`. Hover and focus use the same language. |
| `ob-button` | Variants `primary`, `secondary`, `ghost`, `danger`. Primary has `--ob-primary` with background-color text. Disabled at 55 % opacity. |
| `ob-field` | Label above, background `--ob-bg`, border `--ob-control-border`. `aria-invalid` uses `--ob-danger` plus a message. |
| `ob-tabs` | `@angular/aria` behavior, pixel cursor on the active tab. |
| `ob-gauge` | Segmented, real values only. Unknown shows a neutral striped track. `role="img"` with a text label. |
| `ob-pill` | Status with a 6 px square and a word. |
| `ob-activity` | Indeterminate line in steps. Only during real work. Never implies a percentage. |
| `ob-avatar` | Square with 4 px radius, sizes 32, 40, 64, 128. Initials on `--ob-elevated` when there is no image. Never a fake image. |
| `ob-bubble` | Character: `--ob-surface`. User: `--ob-bubble-user`. Plain text with line breaks, `*emphasis*` as italics, and no HTML. |
| `ob-dialog` | Native `<dialog>`, focus trap from CDK, overlay shadow. |
| `ob-toast` | Bottom corner, 4 seconds, `aria-live="polite"`, never the only source of an error. |
| `ob-empty-state` | 24 px pixel icon, one sentence, one action. |
| `ob-skeleton` | Static stepped blocks for loading. No shimmer. |

Implementation notes (P08.1a):

- `@angular/aria` and `@angular/cdk` are not dependencies of the frontend. `ob-tabs` implements the WAI-ARIA tabs pattern itself (roving tab index, arrow keys, Home and End, automatic activation). `ob-dialog` uses `showModal()`, which makes the rest of the page inert, so Tab stays in the dialog and the browser returns focus when it closes; it has no CDK focus trap. Adding either package is a change of `package.json` and the lock file.
- `ob-menu-item` takes `kind` (`menuitem` or `option`) instead of `role`, so the host element does not also carry the attribute. A selected option has `aria-selected`; a selected menu item has `aria-current`.
- `ob-field` styles the native `input`, `textarea` or `select` projected in it through `styles/global.scss`; the control sets `aria-describedby` to `{fid}-hint` and `{fid}-error`.
- `ob-toast` is the outlet; the `ToastService` queues messages and removes each after 4 seconds. No screen places the outlet: the notification center (P07.5) draws its own toast markup for new notices instead.
- P08.1b: the chat list, conversation, gallery and scenarios screens show `ob-skeleton` while loading and `ob-empty-state` when empty. The conversation's new messages fade in and rise 4 px in 120 ms, and under `prefers-reduced-motion` the typing squares give way to the typing text. The memory, profile, creator, models and resources screens still use plain status and empty notes; no screen places the `ob-toast` outlet. Covered by `tests/e2e/specs/empty-loading.spec.mjs` (passed in the P08 exit, [evidence](verification/2026-11-10-p08-exit.md)).

## Motion

| Case | Motion |
| --- | --- |
| Hover, focus | Background and border, 120 ms, `steps(2)` |
| Panels, dialogs | Fade and 2 px rise, 160 ms, `steps(3)` |
| New message | Fade and 4 px rise, 120 ms |
| Typing indicator | Three squares in steps, only during real generation |

`prefers-reduced-motion: reduce` removes all nonessential motion. The typing indicator then becomes static text: "sta scrivendo…".

## Layout

### Desktop, at least 1024 px

```text
+------------------+-------------------------------------+-----------------+
| LYRVEIL     [+]  | Nerea Falchi            [cerca] [i] | Profilo         |
| [cerca...]       |                                     | Stato rapporto  |
|                  |            sabato 1 novembre        | Ricordi usati   |
| > Nerea    18:40 |  +-----------------------+          | Scenario        |
|   ok, ammetto... |  | ok, ammetto che...    |          | Immagini        |
| Ilario     ieri  |  +-----------------------+          |                 |
|   Il treno...  2 |          +---------------------+    | Pannello        |
| Tessa      lun   |          | Com'è andata?    vv |    | richiudibile    |
|                  |          +---------------------+    |                 |
| [persone][galleria]  sta scrivendo...                  |                 |
| [impostazioni]   | [img] [ Scrivi un messaggio... ] [>]|                 |
+------------------+-------------------------------------+-----------------+
```

Sidebar 320 px. Context panel 320 px, collapsible, remembered per browser. Conversation area fills the rest, with messages capped at 760 px of width.

### Tablet, 640 to 1023 px

Sidebar and conversation. The context panel opens as a dialog.

### Mobile, under 640 px

```text
+---------------------+      +---------------------+
| LYRVEIL         [+] |      | [<] Nerea       [i] |
| [cerca...]          |      |                     |
| Nerea        18:40  |  ->  | +---------------+   |
| Ilario        ieri  |      | | ok, ammetto   |   |
| Tessa          lun  |      | +---------------+   |
|                     |      |    +-------------+  |
| [chat][pers][impost]|      | [img][ Scrivi ][>]  |
+---------------------+      +---------------------+
```

- Separate chat list and full-width conversation.
- Fixed composer that follows the on-screen keyboard through `100dvh` and `visualViewport`.
- Bottom navigation in the list, none in the conversation.
- Secondary panels open on demand.
- Tap targets of at least 44 x 44 px. No horizontal scrolling at 320 px, or at 200 % zoom.

### Implementation notes (P08.2)

- **Shell** (`app.html`, `app.scss`): a grid of the brand row, the navigation and the screen. It is as tall as the
  visible viewport (`--ob-viewport-height`, set by `followVisualViewport` in `core/shell/viewport.ts` from
  `visualViewport`, with `100dvh` as the fallback), and each screen scrolls inside it. From 1024 px the sidebar is 320
  px; from 640 to 1023 px it is 30 % of the width, between 200 and 320 px; under 640 px the navigation is a bar at the
  bottom that wraps to a second row rather than cut a link off, and a conversation (`/chats/:id`) hides the brand row
  and the bar.
- **Sidebar content:** the sidebar holds the brand, the bell and the navigation links. The chat list of the desktop
  mock-up is not embedded in it: the list stays the routed screen **Conversazioni**, so on desktop it fills the area
  beside the sidebar and a conversation replaces it.
- **Conversation** (`conversation-screen.*`): messages and the composer line up in a 760 px column. The context panel
  is delivered with its reachable part only, links to the profile and to the Memory Studio; the relationship in words,
  recent events, images, scenario and proactive settings of `docs/frontend.md` are not drawn. From 1024 px it is a
  320 px column that the **Dettagli** button collapses (remembered in `localStorage`, `lyrveil.context-panel`); below
  that the button opens the same content in an `ob-dialog`. Under 640 px the avatar is dropped from the header and the
  composer is one row, with icons for the image and send buttons (their words remain the accessible names).
- **Margins:** `--ob-gutter` (`styles/global.scss`) is 24 px, and 12 px under 640 px; every screen and dialog pads with
  it. `body` sets `overflow-wrap: anywhere`, so no long word or identifier widens a screen.
- **Tested by** `core/shell/viewport.spec.ts` and `context-panel-storage.spec.ts` (unit), and
  `tests/e2e/specs/responsive.spec.mjs` (T-QLT-04: seven screens and the conversation at 320, 640, 1024 and 1440 px
  and at 200 % zoom, emulated as a viewport of half the size). Passed in the P08 exit, with the other `e2e` specs
  ([evidence](verification/2026-11-10-p08-exit.md)). The general settings and the data settings are the placeholder
  screen, so the check on them covers the placeholder only.

## Accessibility

| Area | Rule |
| --- | --- |
| Standard | WCAG 2.2 AA |
| Keyboard | All actions work without a pointer. See [Frontend](frontend.md#keyboard) |
| Focus | Always visible: 2 px solid `--ob-focus` with 2 px offset. Dialogs trap and return focus. A route change moves focus to the main heading |
| Lists | Chat list is a `listbox`. Message list is a `log` |
| Live regions | A completed reply is announced once. Streaming text is not announced token by token. Job status uses `polite` |
| Color | Status always has words or icons too |
| Loading | Skeletons and status text. A long operation always says what it waits for |
| Motion | Honors reduced motion |
| Language | `<html lang="it">`. Message text carries the conversation language |

Implementation notes (P08.3):

- **Focus ring:** `:focus-visible` in `styles/global.scss` draws it for every control; no stylesheet removes it. A skip link (**Vai al contenuto**) is the first Tab stop, and a change of path moves focus to the screen's `h1` ([Frontend](frontend.md#keyboard) gives the exceptions).
- **Dialogs:** every dialog is a native `<dialog>` opened with `showModal()` (`ob-dialog`, the new-conversation dialog, the image form and the gallery lightbox). The browser makes the page behind it inert, Esc closes it, and focus returns to the control that opened it. `ob-dialog` also wraps Tab from the last control to the first and Shift+Tab the other way, because the browser alone hands focus to its own interface after the last control. The shortcut help (`?`) is one of them.
- **Scrolling screen area:** `main` has `tabindex="0"`, so a keyboard user can scroll a screen whose content has no focusable control (`axe` rule `scrollable-region-focusable`).
- **Live regions:** the shell has one `aria-live="polite"` region (`Announcer`) for a finished reply; toasts are `aria-live="polite"`; the conversation's typing, drawing and queue status, image progress, loading placeholders and the other status lines are `role="status"`, and errors are `role="alert"`. The message list is a `role="log"` with `aria-live="off"`, which is what keeps streaming text from being read token by token.
- **Departure from the table:** the chat list is a list of links with a row menu, not a `listbox`. A `listbox` holds options, not links and buttons, and each row has both, so the list keeps the semantics that match its content.
- **Tested by** `core/shell/shortcuts.spec.ts`, `core/shell/focus.spec.ts` and `features/conversation/chat-find.spec.ts` (unit), and `tests/e2e/specs/accessibility.spec.mjs` (T-QLT-03: `axe` on the ten screens and the keyboard walkthroughs). Passed in the P08 exit ([evidence](verification/2026-11-10-p08-exit.md)); `axe` is run once the entrance animations have finished, because it reads the color of text that is still fading in.

## Interface text

Text is Italian, informal and short. It describes people and actions, not technology. Neutral wording avoids assuming a character's gender.

| Situation | Text |
| --- | --- |
| Empty chat list | Nessuna conversazione. Crea un personaggio per iniziare. |
| Typing | sta scrivendo… |
| Queued during an image | In coda: risponderà appena finisce l'immagine |
| Failed reply | Risposta non riuscita. Riprova |
| Interrupted reply | Risposta interrotta. Puoi rigenerarla |
| Image failed | Immagine non riuscita. Riprova |
| Deleted image | Immagine eliminata |
| Offline backend | Lyrveil non risponde. Sto riprovando… |
| First start notice | I personaggi di Lyrveil sono simulati da modelli di intelligenza artificiale in esecuzione sul tuo computer. |
| Delete memory confirmation | Eliminare questo ricordo? Non verrà ricreato dagli stessi messaggi. |
| Age rule | I personaggi devono essere adulti: indica almeno 18 anni. |

Technical terms appear only in **Impostazioni > Modelli** and **Risorse AI**.

## Verification

P08 checks: no literal colors outside the tokens, automated contrast with `axe` through Playwright, keyboard walkthroughs of all ten screens, reduced motion, widths of 320, 640, 1024 and 1440 px, 200 % zoom, and a visual check of
Pixelify Sans with Italian accents.
