# Demo screenshots

The README screenshots (R-PUB-05) are generated, not captured by hand. They show only the original demo characters
of the [character system](character-system.md#demo-characters) and synthetic data: the gallery uses the image fake of
`tests/e2e/support/images.mjs`, and the conversation text comes from the fake runtimes of the test project.

## Regenerate

With the Compose test project up (see [testing](testing.md)):

```
cd tests/e2e
npm run screenshots
```

The script `tests/e2e/screenshots/capture.mjs` loads the demo characters through `POST /api/v1/characters/demo`, visits
the screens at 1440 x 900 and writes PNG files to `docs/images/screenshots/`: `chats`, `character-profile`,
`scenarios`, `models` and `gallery`. Before it saves a shot it checks the page text and fails on a home path, an
e-mail address or a token-like string, so a leaking screen is never written.

## Status

The script ran against the live test project (fake runtimes) and the five PNG files are committed under
`docs/images/screenshots/`. Each was opened and reviewed: only demo data, no real name, path or token. Limits: the
chats and scenarios screens show their empty state, and the gallery tiles are 1 x 1 pixel fakes stretched to the tile
size. The README shows them.

The first run found that the character list has no `is_demo` flag; the script now finds Nerea by first name.
