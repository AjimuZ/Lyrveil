# Frontend

Status: accepted (2026-11-01). Implementation: partial. P01.5a delivered the zoneless shell, the route table with placeholder screens, the tokens, the bundled fonts, the text catalog with `Translator`, the lint and build gates, and the `Dockerfile` and `nginx.conf` ([evidence](verification/2026-11-06-frontend-shell.md)). P01.5b added the generated API types, the HTTP providers with error mapping and the WebSocket client ([evidence](verification/2026-11-06-api-types-and-socket.md)). P02.6a delivered screen 1, the conversation list, with the minimal character form (`features/chats/`); it uses the HTTP layer. P02.6b delivered screen 2, the conversation (`features/conversation/`), which calls `EventsSocket.connect()` when it opens a chat; the shell does not show connection state yet. P03.2a delivered the Character Creator, screen 3 (`features/characters/`), on Signal Forms. P03.2b delivered the Character Profile, screen 4; the characters list is still a placeholder. Every other real screen is planned, phases P03 to P08.

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
  tools/api-types.mjs         Generates the API types and fails when the committed ones are out of date
  tools/eslint/               T-SEC-09 restrictions, their fixtures and test
  src/
    assets/icons/             ASCII icon grids
    i18n/it.json              Interface text
    styles/                   tokens.scss, reset.scss, typography.scss, global.scss
    app/
      app.ts  app.routes.ts  app.config.ts
      core/
        api/                  generated/ (types), api-client.ts, providers.ts (provideApi), api-error.ts
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

### API types

`@hey-api/openapi-ts` 0.99.0 generates `src/app/core/api/generated/` from `backend/openapi.json`, with the `@hey-api/typescript` plugin only: the types of the schemas and of each operation's request and responses. The HTTP calls are not generated. `ApiClient` wraps Angular's `HttpClient` under `/api/v1` and the caller names the response type from the generated ones. `provideApi()` in `app.config.ts` registers `HttpClient` on `fetch` with an interceptor that rethrows every failure as an `ApiError`.

- `npm run api:generate` rewrites the generated folder from the committed document. Run it after the backend changes the document ([Backend](backend.md#database-and-health)), and commit the result.
- `npm run lint` ends with `tools/api-types.mjs check`, which generates into a temporary folder and fails when any file differs from the committed one. The document is read from the repository, not from a running backend, so the check needs no Docker.
- ESLint does not lint the generated folder.

### API errors

`toApiError` maps what a request throws to an `ApiError` with `code`, `status`, the server's `detail`, `fieldErrors` and `textKey`, the catalog key of the Italian message (`translator.t(error.textKey)`). The body's `code` wins when it is one of the REST codes of [APIs](api.md#errors). Without one, the status decides: 404, 409, 413, 415 and 422 map to their code; no response and the proxy's 502, 503 and 504 map to `NETWORK`, "Lyrveil non risponde. Controlla che sia avviato e riprova."; any other 5xx is `INTERNAL`, and the rest is `UNKNOWN`. The server's `detail` is kept for the screen to show, such as the download command of `MODEL_PROFILE_UNAVAILABLE`. The error's `message` never holds server text.

### Realtime handling

`EventsSocket` implements the protocol of [APIs](api.md#event-websocket). A store calls `on('message.created')` for an event type and subscribes to `resync$` to reload through REST. `state` is a signal for the connection notice.

1. Connect to `/ws`, then send `hello` with `last_event_id` and `boot_id`. The `boot_id` comes from the server's `connected` frame.
2. Reconnect with backoff of 1, 2, 5 and 10 seconds. The delay restarts at 1 second once a connection has served a frame.
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

Delivered in P02.6a (`features/chats/chat-list-screen.ts`): search (debounced 250 ms, over `GET /conversations?q=`), the **Attive** and **Archiviate** tabs, a per-row menu with pin, archive or unarchive, and delete, and a **Ripristina** notice after a delete. **Nuova conversazione** opens `new-chat-dialog.ts`: a character, a conversation language (or the character's) and **Inizia conversazione**, plus **Nuovo personaggio**, a minimal form (name, optional last name, age 18 to 120, **Come parla di sé**, language) that the Creator replaces in P03. A row links to `/chats/:conversationId`, the conversation screen of P02.6b. Not shown because nothing backs them yet: last-message preview, unread count (P07), typing and queue hints, avatar images (P03). The scenario choice joined the picker in P03.7a (see screen 7). Previews and the avatar take their place when those exist. The row's model types in `chats.model.ts` are written by hand until `backend/openapi.json` includes the conversation and character routes.

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

Delivered in P02.6b (`features/conversation/`): the route `/chats/:conversationId` loads `conversation-screen.ts`, which
provides its own `ConversationStore` and opens the chat named by the route.

- **Delivered:** the header with the avatar initial, the name and the status (**Sta scrivendo…** or **In coda**); messages
  under day separators and with a `HH:mm` time; one check for a sent message (the server never reports `read` yet); the
  typing indicator and the queue note in a reply bubble that has no text yet; bubble actions **Copia**, **Modifica**,
  **Elimina** (with an inline confirmation), and on the latest reply **Rigenera** and `‹ 2/3 ›` alternatives, shown on
  hover and keyboard focus and always shown where there is no hover; the composer with Enter to send, Shift+Enter for a
  line break, a counter from 7000 characters and a refusal over 8000; **Mostra messaggi precedenti**, which also loads
  when the list is scrolled to the top; a failed send that stays with **Riprova invio** and **Scarta**.
- **Streaming and recovery:** `message.delta` pieces are batched per animation frame into one signal for all streaming
  replies. The store applies a piece by its `offset` (`applyDelta`): the next piece appends, a repeat changes nothing,
  and a piece that starts beyond the known text waits. That is the page reload during streaming: REST returns the text
  flushed every 2 seconds, the live pieces start beyond it, and the store asks REST for the newest page every 2 seconds
  until the stored text reaches them. The same reload runs while the socket is not open and a reply is still produced,
  and after `resync.required`. `message.completed` replaces the message with its final text. The send, edit, delete,
  regenerate and activate answers are applied from the response, because the REST routes publish no events yet.
- **Images (P05.5b)** (`image-dialog.*`, `image-request.ts`, `conversation-store.ts`). The composer's **Chiedi
  un'immagine** opens a modal form with the request (prefilled with the text in the composer), **Formato**
  (**Automatico** by default, then square, portrait, landscape, tall and wide), **Abito** (**Lascia decidere alla
  scena**, **Abituale** and the named outfits read from `GET /characters/{id}`), **Qualità** (**Alta** by default, or
  **Rapida**) and, under **Impostazioni avanzate**, seed, steps and CFG. `validateImageForm` checks the API's limits
  (request 1 to 1000 characters, seed 0 to 4294967295, steps 10 to 50, CFG 1 to 12; the optional fields may be empty)
  and a field outside them shows its reason after the first send, which then does not leave the browser. A refusal by the
  server stays in the dialog. A sent request closes it, and the answer adds the user's message and the image placeholder
  (`kind: image`, `queued`) to the list. The store keeps the latest `image.progress` of each placeholder: its bubble
  shows the phase (**Preparo la scena…**, **Carico il modello per le immagini…**, **Disegno l'immagine (prima
  passata)…**, **Rifinisco i dettagli (seconda passata)…**, **Salvo l'immagine…**), a bar and **14 di 28** while the
  phase counts steps, and the header says **Sta disegnando…**. `message.completed`, `image.completed`, `message.failed` or
  a deleted message drop the progress; a progress event for an image that is already final is ignored. A finished image
  message shows its thumbnail (`/media/images/{id}/thumbnail`, a link to the original in a new tab) above the caption;
  a failed one says **L'immagine non è riuscita. Puoi chiederla di nuovo.** **Rigenera** and the alternatives are not
  offered on an image message, because regenerating an image belongs to the gallery (P05.6). There is no cancel action
  yet: the job routes of cancellation arrive in P06.3. Tests: `image-request.spec.ts` and the "image requests" block of
  `conversation-store.spec.ts` (unit, written and not run in P05.5b), and `tests/e2e/specs/image-request.spec.mjs`
  (written, not run).
- **Avatar generation (P05.5b)** (`avatar-tools.ts`, in the profile and in the Creator's Images tab). **Genera un
  avatar** sends `POST /characters/{id}/assets/generate` with `purpose: avatar`, then reads `GET /jobs/{id}` every second
  (the backend publishes no `job.updated` yet) and shows **In coda: posizione N**, then the stage (**Preparo la scena
  dell'avatar…**, **Disegno l'avatar…**, **Salvo l'avatar…**). When the job completes the list is read again; a generated
  variant has a **Generato** mark. The server makes the new avatar primary only when the character had none; otherwise
  **Rendi principale** chooses it. A failed job shows its Italian reason. Leaving the screen stops the polling.
- **Message text** is bound as text and drawn with `white-space: pre-wrap`; no template uses `innerHTML` (T-SEC-03, T-SEC-09).
- **Not delivered, so no control is drawn for it:** the context panel, the lightbox, regenerating or cancelling an
  image, the contextual proposal card, search in a chat, "Ricorda questo", the simulated `read` state, unsent text kept per chat in `localStorage`, and
  virtual scrolling (`@angular/cdk` is not a dependency yet; the list renders every loaded page). The chat list does not
  navigate to a new chat after **Inizia conversazione**.
- **Direct visits to a deep route.** `index.html` has no `<base>` element, because the Content-Security-Policy says
  `base-uri 'none'` and the browser refused it, so a reload on `/chats/:conversationId` loaded the assets from
  `/chats/` and showed an empty page. The build now writes absolute asset URLs (`deployUrl: "/"` in `angular.json`) and
  `app.config.ts` provides `APP_BASE_HREF`. `tests/e2e/specs/zz-diagnose.spec.mjs` guards it.
- **Tests:** `conversation-store.spec.ts` and `day-label.spec.ts` (unit) and, in `tests/e2e/specs/`,
  `conversation.spec.mjs` (T-SEC-03, T-MSG-05, T-MSG-06, T-MSG-08) and `conversation-stream.spec.mjs` (T-MSG-03, T-MSG-04,
  T-MSG-07, T-MSG-09, T-MSG-12); see [Testing](testing.md#messaging). The unit test for the screen component itself was
  not written.

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

Delivered in P03.2a (`features/characters/`): the routes `/characters/new` and `/characters/:id/edit` load
`character-creator-screen.ts`, a Signal Forms editor of the whole card.

- **Form.** The form model is one signal (`card-form.ts`: `CreatorModel`, `toModel`, `toCard`, `creatorSchema`). Each rule
  is a pure function in `card-rules.ts` that mirrors `lyrveil.domain.card` and `lyrveil.services.card_rules`; the schema
  attaches them to the fields, and a refusal carries the Italian text of the catalog (`creator.error.*`), so the form
  refuses what the API refuses: an age under 18 or over 120, a name outside 1 to 80 characters, an unknown vocabulary
  tag, a free tag of more than 4 words, a reserved (`usual`, `scene`), malformed or repeated outfit key, an example with
  no answer, a seed policy without a starting seed, and the length and count limits. The server stays the authority and
  its `detail` is shown when it refuses a save. A message shows once its field is touched, and a save with a wrong field
  switches to the first tab with a problem and focuses it. One difference: the starting seed is limited to
  9007199254740991 (a browser number), where the server accepts up to 2^63 - 1.
- **Tabs.** Identità (name, age, **Come parla di sé**, language, narrative identity, occupation, setting, biography,
  background, interests), Personalità (traits by category, each lockable), Comunicazione (register, length, questions,
  initiative, expressiveness, *tu* or *Lei*, narration and emoji with a one-line example, punctuation, colloquial style,
  example pairs in an order the user sets, recurring expressions, **Da evitare**), Aspetto (the eight Italian
  descriptions with their locks, the subject tag, the closed hair color, hair length, eye color and skin, vocabulary
  chips for hairstyle, face, body, marks, accessories and style, free tags, **Prompt persistente**, the usual outfit,
  up to 5 outfits, the persistent negative and the seed policy). Choosing **Al femminile**, **Al maschile** or **Non
  indicato** proposes the subject tag, and a formal register proposes *Lei*, until the user chooses either by hand.
- **Vocabulary.** `GET /characters/vocabulary` supplies the tags; `Vocabulary.search` matches the English tag or an
  Italian alias, ignoring case and accents. The chip control (`tag-picker.ts`) is a Signal Forms custom control.
- **Draft and leaving.** A new character keeps a local draft in `localStorage` (`lyrveil.character-draft`) until it is
  saved; reopening `/characters/new` restores it and offers **Scarta la bozza**. Leaving with unsaved changes asks for
  confirmation (`leave-guard.ts`). After a first save the screen moves to the character's edit address.
- **Assist panel** (P03.3). **Bozza assistita** above the tabs takes a description of up to 2000 characters and sends
  `POST /characters/drafts`, with `character_id` on the edit screen. The screen then reads `GET /jobs/{id}` every second
  and shows **In coda: posizione N** or **Il modello sta scrivendo la bozza…**. A completed draft fills every field, but
  the form stays unsaved (**Modifiche non salvate**, the leave guard, the local draft): only **Salva personaggio**
  creates or saves the character, through the normal routes. A note lists, by their labels, the fields the model
  proposed (`proposed_fields`), instead of a "proposto" mark on each field. On the edit screen the server has already
  kept the locked traits and fields. A failed draft shows the server's Italian reason, such as "L'età deve essere almeno
  18.", and leaves the form as it was. The draft replaces what the form held; there is no merge. The job is followed by
  polling, because the backend does not publish `job.updated` yet.
- **Not delivered, so no control is drawn for it:** import and export, avatar generation, the counter of how many examples enter the prompt, the first-chunk warning of
  **Prompt persistente** (the prompt builder arrives with P05, so the text is the plain list of the chosen tags), and
  the locks' effect on a tag translation (there is no such job yet). The characters list is still a
  placeholder; the new-chat dialog links to the Creator.
- **Tests:** `card-rules.spec.ts`, `card-form.spec.ts` and `character-creator-screen.spec.ts` (unit, with the assist panel
  since P03.3), and
  `tests/e2e/specs/character-creator.spec.mjs` (T-CHR-13); see [Testing](testing.md#character-creator). The E2E spec
  was written but not run in this change.

### 4. Character Profile

Avatar and variants, identity summary, personality, communication examples, conversation list, **Nuova conversazione**, **Modifica**, **Memoria**, version history with compare and restore, proposals for the profile, duplicate, archive and purge. A clear note states that the character
is simulated.

Delivered in P03.2b (`character-profile-screen.*`, route `/characters/:id`):

- **Content.** The identity summary (age, how it speaks of itself, language, and the written fields that are not empty),
  the traits with their lock, the example exchanges, the character's active conversations (a link each) and a note that the
  character is simulated. A route change to another id, as after **Duplica**, loads that character in the same component.
- **Actions.** **Nuova conversazione** starts a chat and opens it (disabled while archived). **Modifica** opens the Creator.
  **Duplica** creates "<name> (copia)" and opens it with a notice that conversations and memories were not copied.
  **Archivia** and **Rimuovi dall'archivio** switch the status and show a note while archived. **Elimina
  definitivamente** reads `GET .../purge-preview` and opens an inline confirmation (`role="alertdialog"`) with the counts
  and a field for the first name; its button stays disabled until the typed name equals the first name, and the server
  checks it again. After the purge the screen goes to `/chats`. A refusal shows the server's `detail` and changes
  nothing. One action runs at a time.
- **Versions.** The list shows each version with its number, the "Attuale" pill, the actor (Modifica, Ripristino,
  Duplicazione, Importazione, Proposta), the summary and the time. Every version but the current one has **Confronta**,
  which shows a table of the fields that differ from the current version (Italian field names from the Creator's
  labels, `version-diff.ts`), and **Ripristina**, which adds a new version and reads the character again.
- **Avatar tools** (`avatar-tools.ts`, also in the Creator's Images tab for a saved character): file choice with a
  preview before sending, crop of a variant by size and position sliders (a new 512 x 512 variant; the original stays),
  variant list and **Rendi principale**. Errors show the catalog text of the code. The conversation list shows the
  primary avatar. Deleting a variant is not offered.
- **Memoria** opens the [Memory Studio](#5-character-memory-studio) of the character.
- **Not delivered, so no control is drawn for it:** generating images other than the avatar (profile image, scenes,
  variants, the gallery: P05.6) and the proposals for the profile. Avatar generation is described under screen 2.
- **Tests:** `character-profile-screen.spec.ts` and `version-diff.spec.ts` (unit), and
  `tests/e2e/specs/character-profile.spec.mjs` (T-CHR-04, T-CHR-08). The end-to-end spec was written but not run in this
  change.

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

Delivered in P04.9a (`features/memory/`): the route `/characters/:id/memory` loads `memory-studio-screen.ts`, and the
profile's **Memoria** link goes to it. Four tabs are drawn, **Ricordi**, **Eventi**, **Rapporto** and **Riassunti**,
and **Diagnostica** while diagnostic mode is on (P04.9b, below); **Conflitti** and **Proposte** are not (their routes
are later), and no control is drawn for them. A **Conversazione** choice at the top limits the memories and events to one chat (the
`conversation` query parameter chooses it on entry); **Rapporto** and **Riassunti** need one chat and say so
otherwise. The state lives in `memory-store.ts`; every action calls the REST API and reads the list again.

- **Ricordi.** Search (debounced 250 ms, by stem) and the filters **Stato** (default **In uso**, active and uncertain),
  **Tipo**, **Origine** and **Blocco**; pages of 50 with **Mostra altri ricordi**. A card shows the text, pills for the
  kind, origin and any state other than active (**Incerto**, **Sostituito**, **Scaduto**, **Da rivalutare**), **Bloccato**,
  **Da rivedere** and **Temporaneo**, then the importance, the confidence in percent, the number of source messages and the
  time of the last change. A memory in an unresolved contradiction (`uncertain` with a conflict group) also says so in a
  note. **Modifica** opens the text (1 to 280 characters) and the importance in place; **Blocca**/**Sblocca**;
  **Conferma** for a memory in use that is not confirmed; **Elimina** asks for an inline confirmation first.
  **Fonti e cronologia** opens, under the card, the source messages (number, the start of the text read, a flag when the
  message was changed or deleted, and a link to the chat), the memories it contradicts, and its history from the
  revisions (version, who, why, when, and the text it had). A refused action shows the server's reason and changes
  nothing. Not drawn: **Condividi** (shared memories are not written yet) and manual creation (the API has it).
- **Eventi.** The character's episodes, newest first, with the date, participants, importance, links to other episodes
  (**Ha causato**, **Causato da**, **Viene dopo**, **È seguito da**, **Collegato a**) and source messages.
- **Rapporto.** The description in words first (the narrative, or the four bands), then expectations, limits, mood,
  topic, open threads, goals, changes of the story and recent events when they exist, then the change log with each
  reason, kind and source messages. The four numbers are in a `details` section labeled **Valori numerici (avanzato)**.
  A chat whose state was never written says so. Corrections are not drawn (the route is not delivered).
- **Riassunti.** Every checkpoint, oldest range first: message range, level, version, status, text, its lists (events,
  decisions, relationship changes, open questions, key facts) and the messages it rests on.
- **Diagnostica** (P04.9b). The tab is drawn only while `app.diagnostic_mode` is on, read from `GET /settings` when the
  screen opens (off when the read fails); with the mode off no control or request for it exists. It needs one chosen
  conversation. A **Risposta** choice lists the replies of that chat that have a stored assembly, newest first, each
  with its message number and the start of its text. For the chosen reply the tab shows the tokens of the prompt
  against the window; the **Budget** (window, space reserved for the reply, safety margin, space for the prompt,
  targets for memories and summaries); the tokens of each block; the active corrective notes, the reductions applied
  and any uncovered message ranges; every memory in the prompt with its text now (or that it was deleted since), its
  label, score and the nine parts of the score in a `details`; the summaries used; and **Testo completo del prompt**
  in a `details`, which says so when the text was not kept. The store is `memory-store.ts` (`diagnosticMode`,
  `replies`, `diagnostics`), the data comes from `GET /conversations/{id}/diagnostics` and
  `GET /messages/{id}/diagnostics` ([API](api.md#messages)). Not drawn: the switch for the mode (see
  [General settings](#8-general-settings); until it exists the mode is changed with `PATCH /settings`) and the link per
  reply in the conversation's context panel.
- **Tests:** `memory-store.spec.ts` and `memory-studio-screen.spec.ts` (unit), and `tests/e2e/specs/memory-studio.spec.mjs`
  (memories written by hand: list, filter, search, edit with a new revision, lock, delete, and the empty states; and the
  diagnostic mode: off by default, then the assembly of one reply). The end-to-end spec was written but not run in this
  change; the fake runtime never produces extraction output, so sources, events, state changes, summaries and a
  contradiction are covered by the backend integration test (T-MEM-08, T-MEM-11) and the unit specs.

### 6. Gallery

Grid of thumbnails with virtual scrolling. Filters by character, conversation, purpose and date. The lightbox has metadata (prompt, scene, outfit, aspect, seed, steps, CFG, model, workflow), regenerate with advanced options, a link to the source message, delete and save-as-avatar. The advanced options refuse `embedding:` and weights outside 0.5 to 1.5 with an Italian explanation.

Delivered in P05.6 (`features/gallery/`): the route `/gallery` loads `gallery-screen.ts`. The grid shows thumbnails only
(`/media/images/{id}/thumbnail`, loaded lazily by the browser, with `content-visibility: auto` on each cell), 24 per page,
and **Carica altre** requests the next page by the opaque cursor of `GET /images`. The filters are **Personaggio** and
**Tipo** (the purpose). A thumbnail opens the lightbox, a modal `<dialog>` that closes with Escape: the original, the
character, purpose, request, final prompts, aspect and size, seed, steps, CFG, profile and preset, and workflow; a link to
the source chat; **Rigenera** with **Mantieni il seed** (`POST /images/{id}/regenerate`, a new variant in the source chat,
offered only for an image that has a chat); and **Elimina** with an inline confirmation. The files are read from
`data/media`, so the gallery works with ComfyUI off. A character's primary avatar cannot be deleted (`409`, with the
reason on screen).
Not delivered: the conversation and date filters (the route takes `conversation_id`, the screen does not offer it),
virtual scrolling (paging plus lazy thumbnails instead), the advanced regenerate dialog with editable prompts, and
save-as-avatar (an avatar is chosen from the profile's avatar tools).
- **Tests:** `gallery-store.spec.ts` (unit) and `tests/e2e/specs/gallery.spec.mjs`. The end-to-end spec drives the
  real screen over a Playwright fake of the image routes, because the Compose test project draws nothing before P05.7; the
  real routes, rows and files are covered by `test_the_gallery_pages_filters_serves_thumbnails_and_deletes`. The specs
  were written but not run in this change.

### 7. Scenarios

List with search, edit form (title, description, setting, opening message, optional character, language) and archive. **Inizia conversazione** opens the new chat picker. A note says that existing conversations keep their copy.

Delivered in P03.7a (`features/scenarios/`): the route `/scenarios` loads `scenarios-screen.ts`. **Nuovo scenario** opens
the form above the list (the title is required; a refusal of the server, such as a text about a minor, stays on the form
with its reason); each card has **Inizia conversazione** (an active scenario only), **Modifica**, **Archivia** or
**Ripristina**, and **Elimina** with an inline confirmation. Search (debounced 250 ms, over `GET /scenarios?q=`) and the
**Attivi** and **Archiviati** tabs work as on the chat list. **Inizia conversazione** goes to `/chats?scenario=<id>`, where
the list opens the new chat picker with that scenario chosen (and its character, when the scenario is for one) and then
drops the parameter. The picker has a **Scenario** choice with the scenarios the chosen character can use. Not
delivered: scenario text in the conversation's context panel (that panel is planned).

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
2. `nginxinc/nginx-unprivileged:1.30.6-alpine`: copies `dist/lyrveil/browser` and `nginx.conf`, as `conf.d/default.conf`, port 8080, user `10001:10001` instead of the base image's user 101.

`nginx.conf` serves `index.html` for client routes, caches hashed files for a year, proxies `/api`, `/media` and `/ws` to `lyrveil-backend:8000`, passes the original `Host` and `Origin`, disables proxy buffering on `/ws`, limits bodies to 80 MB, answers `GET /healthz` for the Compose health check, and sets the security headers, dropping the backend's copies so each arrives once ([Security](security.md#http-headers)). The image has not been built yet: see the [evidence](verification/2026-11-06-frontend-shell.md#not-executed).

## Tests

- Unit and component: Vitest through `ng test`, with `TestBed` and component harnesses, in jsdom. `npm run test` also runs the Node tests of `tools/`, T-SEC-09 and the API types check among them. The socket tests replace the browser's `WebSocket` through the `EVENTS_SOCKET_FACTORY` token and drive the timers with Vitest's fake timers. `scripts\lyrveil.cmd test unit` runs both.
- End-to-end: Playwright against the fake runtimes. See [Testing](testing.md).
- Accessibility: `@axe-core/playwright` on all ten screens.
- `ng build` must pass with no warnings before any commit that touches `frontend/`. `scripts\lyrveil.cmd test lint` runs `npm run lint` (ESLint with no warnings allowed, then the color check, then the API types check) and `npm run build` (`tools/build-strict.mjs`). The build fails on any line with `WARNING`, on a `url()`, `@import`, `src` or `href` that names another origin, and when a font the CSS names is missing from the output.
