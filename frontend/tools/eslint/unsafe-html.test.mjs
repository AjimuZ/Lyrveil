// T-SEC-09: the project's ESLint configuration reports every unsafe HTML construct in the fixtures, and only those.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { ESLint } from 'eslint';

const root = fileURLToPath(new URL('../..', import.meta.url));

/** Lints one fixture with the real configuration and returns the lines of its no-restricted-syntax reports. */
async function restrictedLines(fixture) {
  // ignore: false lifts the configuration's ignore of the fixtures folder for this explicit run.
  const eslint = new ESLint({ cwd: root, ignore: false });
  const [result] = await eslint.lintFiles([`tools/eslint/fixtures/${fixture}`]);
  const fatal = result.messages.filter((message) => message.fatal);
  assert.deepEqual(fatal, [], `${fixture} must parse`);
  return result.messages.filter((message) => message.ruleId === 'no-restricted-syntax').map((message) => message.line);
}

test('template bindings to innerHTML and outerHTML are reported, text interpolation is not', async () => {
  assert.deepEqual(await restrictedLines('unsafe-html.html'), [2, 3, 4]);
});

test('innerHTML, insertAdjacentHTML, HTML host bindings and bypassSecurityTrust* are reported in TypeScript', async () => {
  const lines = await restrictedLines('unsafe-html.ts');
  assert.deepEqual([...lines].sort((a, b) => a - b), [7, 8, 11, 16, 17, 18]);
});
