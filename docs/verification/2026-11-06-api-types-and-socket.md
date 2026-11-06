# API types, HTTP providers and WebSocket client, 2026-11-06

Scope: roadmap item P01.5b: the committed OpenAPI document and its backend test, the generated API types and the lint check on them, `ApiClient` with `provideApi()` and the error mapping, and `EventsSocket` ([Frontend](../frontend.md), [APIs](../api.md), [ADR 0016](../decisions/0016-realtime-websocket-channel.md)).

Machine: Windows 11 Pro, Node.js 24 with its npm, Docker Desktop for the backend test stage. Commands ran from Git Bash in the repository root.

## Lock

| Command | Result |
| --- | --- |
| `npm install --save-dev --save-exact --ignore-scripts --no-audit --no-fund @hey-api/openapi-ts@0.99.0 --prefix frontend` | Added 42 packages and the exact pin `@hey-api/openapi-ts` 0.99.0 to `devDependencies` of `frontend/package.json`, with the lock updated |

## Results

| Command | Exit | Result |
| --- | --- | --- |
| `npm run api:generate --prefix frontend` | 0 | Wrote `src/app/core/api/generated/index.ts` and `types.gen.ts` from `backend/openapi.json`: 7 schema types and the request and response types of the two health operations |
| `npm run api:check --prefix frontend` | 0 | 0 differences from a fresh generation |
| `scripts/lyrveil.cmd test unit tests/unit/test_openapi_document.py` | 0 | Backend: 4 tests passed. The committed `backend/openapi.json` equals `create_app(...).openapi()` |
| `npm run test --prefix frontend` | 0 | Vitest: 5 files, 28 tests passed. `node --test "tools/**/*.test.mjs"`: 11 tests passed |
| `scripts/lyrveil.cmd test lint` | 0 | Documentation checks, ruff, `mypy --strict` for both Python components, ESLint, the color check, the API types check, and the strict build with 0 problems. Initial files 255.93 kB raw, 70.26 kB estimated transfer |

`backend/openapi.json` was written by hand from the FastAPI and Pydantic output rules, because Python could not run on the host. The backend test above is what shows it equal to the application's document; it passed on the first run. The `openapi` stage of `backend/Dockerfile`, which regenerates the file, was written but not built.

## What the tests cover

- Backend, `test_openapi_document.py`: the committed document equals the application's, with the regeneration command in the failure message; the rendering is stable, ends with one newline and has no carriage return; the script writes the rendered document; the script asks for a path.
- Frontend lint: `tools/api-types.mjs check` is the last step of `npm run lint`. `tools/api-types.test.mjs` shows that equal folders pass whatever their line ends, that a changed, a missing and an extra file are each reported, and that the committed types equal a fresh generation.
- `events-socket.spec.ts`: hello with no position on first connection, delivery by event type; frames that are malformed, of another version or of an unknown type are ignored; reconnection after 1, 2, 5, 10 and 10 seconds; the delay restarting after a served frame; replay from the last delivered ID with the announced `boot_id`, and a repeated ID not delivered twice; `resync.required` reaching `resync$` and not the events; a resync when a reconnection has no ID; one resync when the `boot_id` changes; the ping; `disconnect` and a later `connect`; callbacks of a replaced socket ignored.
- `api-error.spec.ts`: every code has an existing Italian message in the catalog; the problem+json body maps to code, detail and field errors; the body's code wins over the status; the status fallbacks; no response gives `NETWORK`; an unknown error gives `UNKNOWN` without its text in `message`.
- `api-client.spec.ts`: typed reads under `/api/v1` with unset query values left out, the four write methods, and `ApiError` reaching the subscriber for a problem body and for a broken connection.

## Not executed

| Item | Reason | Follow-up |
| --- | --- | --- |
| `docker build -f backend/Dockerfile --target openapi --output type=local,dest=backend .` | Written for regeneration after an API change; only the test stage ran | First API change after P01.5b |
| `EventsSocket` against a real server or a browser `WebSocket` | The server side of `/ws` does not exist yet, and the tests use a scripted socket | P02, with T-MSG-12 |
| The `connected` frame with the `boot_id` | The protocol of docs/api.md does not say how the client learns it; this client assumes the frame, written in [APIs](../api.md#event-websocket) | P02, when the server sends it |
| The error interceptor against nginx's real 502 and 503 answers | The proxy runs only in the frontend image, which has not been built | P01.6 |
