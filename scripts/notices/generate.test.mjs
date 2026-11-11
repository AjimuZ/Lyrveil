// Protects the license policy and the deterministic output of the notices generator.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build, isAllowed, normalizeName, parseNpmLock, parsePipCsv, parseUvLock } from './generate.mjs';

test('policy allows the listed licenses and refuses the others', () => {
  for (const ok of ['MIT', 'MIT License', 'BSD-3-Clause', 'BSD License', 'Apache-2.0', 'Apache Software License', 'PSF-2.0', 'ISC', 'Zlib', 'Unlicense']) {
    assert.equal(isAllowed(ok, 'pkg'), true, ok);
  }
  for (const bad of ['MPL-2.0', 'Mozilla Public License 2.0 (MPL 2.0)', 'GPL-3.0', 'AGPL-3.0', '0BSD', 'MIT-CMU', 'UNKNOWN']) {
    assert.equal(isAllowed(bad, 'pkg'), false, bad);
  }
});

test('OR needs one allowed side, AND needs all', () => {
  assert.equal(isAllowed('MIT OR GPL-3.0', 'pkg'), true);
  assert.equal(isAllowed('MIT AND GPL-3.0', 'pkg'), false);
});

test('OFL is allowed for fonts only, and a reviewed entry overrides the policy', () => {
  assert.equal(isAllowed('OFL-1.1', '@fontsource/inter'), true);
  assert.equal(isAllowed('OFL-1.1', '@fontsource-variable/inter'), true);
  assert.equal(isAllowed('OFL-1.1', 'other'), false);
  assert.equal(isAllowed('MPL-2.0', 'certifi', [{ package: 'certifi', license: 'Mozilla Public License 2.0 (MPL 2.0)' }]), false);
  assert.equal(isAllowed('0BSD', 'tslib', [{ package: 'tslib', license: '0BSD' }]), true);
});

test('parsers read pip CSV, uv.lock and npm lock', () => {
  assert.deepEqual(parsePipCsv('"Name","Version","License"\n"a_b","1","MIT"\n'), [{ name: 'a_b', version: '1', license: 'MIT' }]);
  assert.equal(normalizeName('A_b.c'), 'a-b-c');
  const uv = '[[package]]\nname = "x"\nversion = "1"\n\n[[package]]\nname = "me"\nsource = { virtual = "." }\n';
  assert.deepEqual(parseUvLock(uv), ['x']);
  const lock = JSON.stringify({ packages: { '': {}, 'node_modules/a': { version: '1', license: 'MIT' }, 'node_modules/b': { version: '2', dev: true } } });
  assert.deepEqual(parseNpmLock(lock), [
    { name: 'a', version: '1', license: 'MIT', dev: false },
    { name: 'b', version: '2', license: 'UNKNOWN', dev: true },
  ]);
});

test('build is deterministic and the reports cover the lock files', () => {
  const first = build();
  const second = build();
  assert.equal(first.text, second.text);
  assert.deepEqual(first.problems, []);
});
