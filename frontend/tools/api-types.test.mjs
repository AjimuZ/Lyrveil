// Unit tests of the API types check: what counts as a difference, and that the committed types are current.
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { checkApiTypes, compareFolders, generatorConfig } from './api-types.mjs';

function folderWith(files) {
  const folder = mkdtempSync(join(tmpdir(), 'lyrveil-compare-'));
  for (const [name, text] of Object.entries(files)) {
    mkdirSync(join(folder, name, '..'), { recursive: true });
    writeFileSync(join(folder, name), text);
  }
  return folder;
}

test('folders are equal when they hold the same files with the same text, whatever the line ends', () => {
  const committed = folderWith({ 'a.ts': 'one\r\ntwo\r\n', 'sub/b.ts': 'x\n' });
  const fresh = folderWith({ 'a.ts': 'one\ntwo\n', 'sub/b.ts': 'x\n' });
  try {
    assert.deepEqual(compareFolders(committed, fresh), []);
  } finally {
    rmSync(committed, { recursive: true });
    rmSync(fresh, { recursive: true });
  }
});

test('a changed, a missing and an extra file are each reported', () => {
  const committed = folderWith({ 'same.ts': 'a', 'changed.ts': 'old', 'gone.ts': 'g' });
  const fresh = folderWith({ 'same.ts': 'a', 'changed.ts': 'new', 'added.ts': 'n' });
  try {
    assert.deepEqual(compareFolders(committed, fresh), [
      'gone.ts: committed, but no longer generated',
      'added.ts: generated, but not committed',
      'changed.ts: differs from a fresh generation',
    ]);
  } finally {
    rmSync(committed, { recursive: true });
    rmSync(fresh, { recursive: true });
  }
});

test('the generator reads the backend document and writes types only', () => {
  const config = generatorConfig('/app', '/out');
  assert.match(config.input.replaceAll('\\', '/'), /\/backend\/openapi\.json$/);
  assert.deepEqual(config.plugins, ['@hey-api/typescript']);
  assert.equal(config.output.path, '/out');
});

test('the committed API types equal a fresh generation from backend/openapi.json', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  assert.deepEqual(await checkApiTypes(root), []);
});
