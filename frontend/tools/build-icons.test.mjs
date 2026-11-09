// Unit tests of the icon build: grid validation, determinism, and the check that fails when the built icons drift.
import assert from 'node:assert/strict';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  GRID_DIR,
  NAMES_FILE,
  REQUIRED_ICONS,
  SPRITE_FILE,
  buildIcons,
  checkIcons,
  gridPath,
  parseGrid,
  readGrids,
} from './build-icons.mjs';

const ROOT = fileURLToPath(new URL('..', import.meta.url));

/** A copy of the grids and built files in a temporary folder, removed after fn runs. */
function withCopy(fn) {
  const root = mkdtempSync(join(tmpdir(), 'lyrveil-icons-'));
  try {
    cpSync(join(ROOT, GRID_DIR), join(root, GRID_DIR), { recursive: true });
    mkdirSync(dirname(join(root, NAMES_FILE)), { recursive: true });
    cpSync(join(ROOT, NAMES_FILE), join(root, NAMES_FILE));
    fn(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

const square = (size, rows) => rows.concat(Array(size - rows.length).fill('.'.repeat(size))).join('\n');

test('a grid becomes one rectangle per horizontal run of filled pixels', () => {
  assert.equal(gridPath(['#.##', '....', '.###']), 'M0 0h1v1h-1zM2 0h2v1h-2zM1 2h3v1h-3z');
});

test('grids are checked for size, characters and content', () => {
  const ok = square(16, ['#'.repeat(16)]);
  assert.equal(parseGrid('plus', ok).size, 16);
  assert.equal(parseGrid('plus', ok.replace(/\n/g, '\r\n') + '\r\n').rows.length, 16);
  assert.equal(parseGrid('empty-chat', square(24, ['#'.repeat(24)])).size, 24);
  assert.throws(() => parseGrid('plus', square(16, ['#'.repeat(16)]).split('\n').slice(1).join('\n')), /expected 16 rows/);
  assert.throws(() => parseGrid('plus', square(16, ['#'.repeat(15)])), /row 1 has 15 columns/);
  assert.throws(() => parseGrid('plus', square(16, ['x'.repeat(16)])), /other than/);
  assert.throws(() => parseGrid('plus', square(16, [])), /grid is empty/);
  assert.throws(() => parseGrid('plus', square(24, ['#'.repeat(24)])), /expected 16 rows/);
  assert.throws(() => parseGrid('Bad_Name', ok), /lowercase words/);
});

test('every required icon has a valid grid, and 24 x 24 grids exist only for empty states', () => {
  const grids = readGrids(ROOT);
  const names = grids.map((grid) => grid.name);
  for (const name of REQUIRED_ICONS) {
    assert.ok(names.includes(name), name);
  }
  assert.equal(REQUIRED_ICONS.length, 30);
  assert.deepEqual(names, [...names].sort());
  for (const { name, size } of grids) {
    assert.equal(size, name.startsWith('empty-') ? 24 : 16, name);
  }
});

test('the build is deterministic and the committed icons match the grids', () => {
  assert.deepEqual(buildIcons(ROOT), buildIcons(ROOT));
  assert.deepEqual(checkIcons(ROOT), []);
  const sprite = readFileSync(join(ROOT, SPRITE_FILE), 'utf8');
  assert.equal(sprite.match(/<symbol /g).length, readGrids(ROOT).length);
  assert.match(sprite, /fill="currentColor" shape-rendering="crispEdges"/);
  assert.doesNotMatch(sprite, /#[0-9a-f]{3,8}"/i);
});

test('the check fails when a grid changes without a rebuild, and when the sprite is missing', () => {
  withCopy((root) => {
    rmSync(join(root, SPRITE_FILE));
    assert.equal(checkIcons(root).length, 1);
    writeFileSync(join(root, SPRITE_FILE), buildIcons(root)[SPRITE_FILE]);
    assert.deepEqual(checkIcons(root), []);

    const plus = join(root, GRID_DIR, 'plus.txt');
    writeFileSync(plus, readFileSync(plus, 'utf8').replace('##########', '#########.'));
    const problems = checkIcons(root);
    assert.equal(problems.length, 1);
    assert.match(problems[0], /sprite\.svg is out of date with src\/assets\/icons\/\*\.txt \(first difference at line 23\)/);
  });
});

test('the check fails when a required icon is missing or a grid is invalid', () => {
  withCopy((root) => {
    rmSync(join(root, GRID_DIR, 'gear.txt'));
    assert.match(checkIcons(root)[0], /missing required icons: gear/);
    writeFileSync(join(root, GRID_DIR, 'gear.txt'), 'oops\n');
    assert.match(checkIcons(root)[0], /gear: expected 16 rows/);
  });
});
