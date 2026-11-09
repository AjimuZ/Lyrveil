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

The script is delivered. The PNG files are not committed yet: the script has not been run against a live test project
in this change, so no screenshot is claimed. P09.4 adds them to the README after the first run and a visual review.
