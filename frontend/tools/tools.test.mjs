// Unit tests of the build and style checks: what they report and what they let through.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { cssReferences, findWarnings, htmlReferences, isNetworkReference } from './build-strict.mjs';
import { checkStyles, findColorLiterals } from './check-styles.mjs';

test('color literals are found with their lines; tokens, color-mix, comments and strings are not', () => {
  const text = [
    '.a { color: var(--ob-text); background: color-mix(in srgb, var(--ob-primary) 10%, transparent); }',
    '/* #ffffff in a comment */ // red in a line comment',
    '.b { color: #FFF; border-color: rgba(0, 0, 0, 0.5); }',
    '.c:hover { outline: 1px solid red; content: "white"; transition: background-color 1s; }',
  ].join('\n');
  assert.deepEqual(findColorLiterals(text), [
    { line: 3, literal: '#FFF' },
    { line: 3, literal: 'rgba(' },
    { line: 4, literal: 'red' },
  ]);
});

test('the stylesheets of the app hold no color literal outside the token file', () => {
  assert.deepEqual(checkStyles(fileURLToPath(new URL('..', import.meta.url))), []);
});

test('build warnings are the lines that say WARNING', () => {
  const output = 'Building...\n▲ [WARNING] bundle initial exceeded maximum budget.\nApplication bundle generation complete.';
  assert.deepEqual(findWarnings(output), ['▲ [WARNING] bundle initial exceeded maximum budget.']);
});

test('only references to another origin count as network loads', () => {
  for (const reference of ['https://fonts.example/a.woff2', 'http://x/y.css', '//cdn.example/f.woff2', 'ws://h/']) {
    assert.equal(isNetworkReference(reference), true, reference);
  }
  for (const reference of ['./media/inter.woff2', 'media/a.woff', '/favicon.ico', 'data:font/woff2;base64,AA', '#a']) {
    assert.equal(isNetworkReference(reference), false, reference);
  }
});

test('CSS and HTML references are extracted from url(), @import and attributes', () => {
  assert.deepEqual(cssReferences('@import "a.css"; .x { src: url(./media/a.woff2) format("woff2"), url("b.woff"); }'), [
    'a.css',
    './media/a.woff2',
    'b.woff',
  ]);
  assert.deepEqual(htmlReferences('<link rel="stylesheet" href="styles-ABC.css"><script src="https://x/y.js"></script>'), [
    'styles-ABC.css',
    'https://x/y.js',
  ]);
});
