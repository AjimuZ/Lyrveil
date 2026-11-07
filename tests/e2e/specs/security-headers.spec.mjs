// T-SEC-02: through nginx, the app shell, its assets, API answers and errors all carry the headers of docs/security.md
// (HTTP headers) exactly once, and no CORS header.
import { expect, test } from '@playwright/test';

// nginx.conf names the default port in the WebSocket origins, whatever port the test project publishes.
const DOCUMENTED = {
  'content-security-policy':
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; font-src 'self'; " +
    "connect-src 'self' ws://localhost:4200 ws://127.0.0.1:4200; manifest-src 'self'; base-uri 'none'; " +
    "form-action 'none'; frame-ancestors 'none'; object-src 'none'",
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
  'cross-origin-opener-policy': 'same-origin',
  'cross-origin-resource-policy': 'same-origin',
  'permissions-policy': 'camera=(), microphone=(), geolocation=()',
};

// A file name with the build's content hash, the form nginx caches for a year.
const HASHED_ASSET = /(?:src|href)="\/?([^"]+-[A-Z0-9]{8}\.(?:js|css))"/;

/** Asserts that every documented header appears once with its exact value, and that no CORS header appears. */
function expectDocumentedHeaders(response, label) {
  const headers = response.headersArray().map(({ name, value }) => ({ name: name.toLowerCase(), value }));
  for (const [name, value] of Object.entries(DOCUMENTED)) {
    const values = headers.filter((header) => header.name === name).map((header) => header.value);
    expect(values, `${label}: ${name}`).toEqual([value]);
  }
  const cors = headers.filter((header) => header.name.startsWith('access-control-'));
  expect(cors, `${label}: CORS headers`).toEqual([]);
}

test('the app shell and a client route carry the headers', async ({ request }) => {
  for (const path of ['/', '/chats']) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(200);
    expect(response.headers()['content-type'], path).toContain('text/html');
    expectDocumentedHeaders(response, path);
  }
});

test('a hashed asset, and a missing one, carry the headers', async ({ request }) => {
  const shell = await (await request.get('/')).text();
  const asset = HASHED_ASSET.exec(shell);
  expect(asset, 'the shell names a hashed asset').not.toBeNull();
  const found = await request.get(`/${asset[1]}`);
  expect(found.status()).toBe(200);
  expectDocumentedHeaders(found, asset[1]);

  const missing = await request.get('/missing-AAAAAAAA.js');
  expect(missing.status()).toBe(404);
  expectDocumentedHeaders(missing, 'missing asset');
});

test('an API answer and an API error carry the headers', async ({ request }) => {
  const live = await request.get('/api/v1/health/live');
  expect(live.status()).toBe(200);
  expect(await live.json()).toEqual({ status: 'alive' });
  expectDocumentedHeaders(live, 'health');

  const unknown = await request.get('/api/v1/no-such-route');
  expect(unknown.status()).toBe(404);
  expectDocumentedHeaders(unknown, 'unknown route');
});

test('a write from another origin is refused with FORBIDDEN_ORIGIN and carries the headers', async ({ request }) => {
  const refused = await request.post('/api/v1/health/live', {
    headers: { Origin: 'http://evil.example' },
    data: {},
  });
  expect(refused.status()).toBe(403);
  expect(refused.headers()['content-type']).toBe('application/problem+json');
  expect((await refused.json()).code).toBe('FORBIDDEN_ORIGIN');
  expectDocumentedHeaders(refused, 'refused write');
});
