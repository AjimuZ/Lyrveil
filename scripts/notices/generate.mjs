// Generates THIRD_PARTY_NOTICES.md from the lock files and the license reports, and applies docs/licensing.md.
//
// Usage: node scripts/notices/generate.mjs [--check]
//   (no flag)  rewrites THIRD_PARTY_NOTICES.md; exit 1 when a production license breaks the policy.
//   --check    writes nothing; exit 1 when the file is stale or the policy is broken.
//
// Inputs:
//   - backend/uv.lock, orchestrator/uv.lock: the complete dependency list (coverage is checked against it).
//   - scripts/notices/reports/*.csv: `pip-licenses --format=csv` output, see reports/README.md for the commands.
//   - frontend/package-lock.json, tests/e2e/package-lock.json: versions, `dev` flag and `license` of each npm package
//     (the same data license-checker-rseidelsohn reads from package.json files).
//   - scripts/notices/policy.json: license reviews recorded in an ADR.
// The output has no timestamp, so two runs on the same inputs give the same bytes.

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** Python names are compared in the normalised form of PEP 503. */
export function normalizeName(name) {
  return name.toLowerCase().replace(/[-_.]+/g, '-');
}

/** Parses the quoted CSV written by pip-licenses into [{name, version, license}]. */
export function parsePipCsv(text) {
  const rows = text
    .split(/\r?\n/)
    .filter((line) => line.trim() !== '')
    .map((line) => [...line.matchAll(/"((?:[^"]|"")*)"/g)].map((m) => m[1].replace(/""/g, '"')));
  return rows.slice(1).map(([name, version, license]) => ({ name, version, license }));
}

/** Returns the non-project package names of a uv.lock, normalised. */
export function parseUvLock(text) {
  const names = [];
  for (const block of text.split(/^\[\[package\]\]$/m).slice(1)) {
    const name = /^name = "([^"]+)"/m.exec(block)?.[1];
    const source = /^source = \{ (\w+)/m.exec(block)?.[1];
    if (name && source !== 'virtual' && source !== 'editable') names.push(normalizeName(name));
  }
  return names;
}

/** Reads the packages of an npm lock as [{name, version, license, dev}], one entry per name and version. */
export function parseNpmLock(text) {
  const found = new Map();
  for (const [path, entry] of Object.entries(JSON.parse(text).packages ?? {})) {
    if (path === '' || entry.link) continue;
    const name = path.slice(path.lastIndexOf('node_modules/') + 'node_modules/'.length);
    const key = `${name}@${entry.version}`;
    const dev = entry.dev === true;
    const previous = found.get(key);
    if (previous && !previous.dev) continue;
    found.set(key, { name, version: entry.version, license: entry.license ?? 'UNKNOWN', dev });
  }
  return [...found.values()];
}

const ALLOWED = [
  /^MIT( License)?$/i,
  /^BSD(-[1-4]-Clause)?( License)?$/i,
  /^ISC( License)?$/i,
  /^Apache([- ]2(\.0)?|-2\.0| Software License)( License)?$/i,
  /^PSF(-2\.0)?$/i,
  /^Python-2\.0$/i,
  /^Zlib$/i,
  /^Unlicense$/i,
];
const FONT_LICENSE = /^OFL-1\.1$/i;

/** True when one license name, with no OR/AND, is on the production list. */
function allowedName(name, pkg) {
  const clean = name.trim().replace(/^\(|\)$/g, '');
  if (ALLOWED.some((re) => re.test(clean))) return true;
  return FONT_LICENSE.test(clean) && /^@fontsource(-variable)?\//.test(pkg);
}

/**
 * True when the production policy allows the license expression: an OR needs one allowed side,
 * an AND needs all sides allowed. Unknown names are refused.
 */
export function isAllowed(expression, pkg, reviewed = []) {
  if (reviewed.some((r) => r.package === pkg && r.license === expression)) return true;
  return expression
    .replace(/^\(|\)$/g, '')
    .split(/\s+OR\s+/)
    .some((alt) => alt.split(/\s+AND\s+/).every((name) => allowedName(name, pkg)));
}

function byName(a, b) {
  const x = a.name.toLowerCase();
  const y = b.name.toLowerCase();
  if (x !== y) return x < y ? -1 : 1;
  return a.version < b.version ? -1 : a.version > b.version ? 1 : 0;
}

function table(rows) {
  if (rows.length === 0) return '_None._\n';
  const lines = ['| Package | Version | License |', '| --- | --- | --- |'];
  for (const r of [...rows].sort(byName)) {
    lines.push(`| ${r.name} | ${r.version} | ${r.license.replace(/\|/g, '\\|')} |`);
  }
  return `${lines.join('\n')}\n`;
}

function read(rel) {
  return readFileSync(join(ROOT, rel), 'utf8');
}

/** Builds the Python component: production packages from the prod report, the rest of the full report as dev. */
function pythonComponent(title, project) {
  const all = parsePipCsv(read(`scripts/notices/reports/${project}-all.csv`));
  const prod = parsePipCsv(read(`scripts/notices/reports/${project}-prod.csv`));
  const prodKeys = new Set(prod.map((r) => normalizeName(r.name)));
  const lockNames = new Set(parseUvLock(read(`${project}/uv.lock`)));
  const reported = new Set(all.map((r) => normalizeName(r.name)));
  const problems = [];
  for (const n of [...lockNames].sort()) if (!reported.has(n)) problems.push(`${project}/uv.lock: ${n} has no license report`);
  for (const n of [...reported].sort()) if (!lockNames.has(n)) problems.push(`${project} report: ${n} is not in uv.lock`);
  return {
    title,
    source: `${project}/uv.lock`,
    prod: all.filter((r) => prodKeys.has(normalizeName(r.name))),
    dev: all.filter((r) => !prodKeys.has(normalizeName(r.name))),
    problems,
  };
}

function npmComponent(title, dir) {
  const rows = parseNpmLock(read(`${dir}/package-lock.json`));
  return {
    title,
    source: `${dir}/package-lock.json`,
    prod: rows.filter((r) => !r.dev),
    dev: rows.filter((r) => r.dev),
    problems: [],
  };
}

/** Returns {text, violations, problems}: the file content and what the policy or the coverage check refused. */
export function build() {
  const policy = JSON.parse(read('scripts/notices/policy.json'));
  const components = [
    pythonComponent('Backend (Python)', 'backend'),
    pythonComponent('Orchestrator (Python)', 'orchestrator'),
    npmComponent('Frontend (npm)', 'frontend'),
    npmComponent('End-to-end tests (npm)', 'tests/e2e'),
  ];
  const violations = [];
  const problems = components.flatMap((c) => c.problems);
  for (const c of components) {
    for (const r of c.prod) {
      if (!isAllowed(r.license, r.name, policy.reviewed)) {
        violations.push(`${c.title}: ${r.name} ${r.version} uses ${r.license}`);
      }
    }
  }
  violations.sort();

  const out = [
    '# Third-party notices',
    '',
    '<!-- Generated by scripts/notices/generate.mjs. Do not edit by hand. -->',
    '',
    'Lyrveil is licensed under the [Apache License 2.0](LICENSE). It depends on the packages below, each under its own license.',
    'The policy is in [docs/licensing.md](docs/licensing.md#policy). Python data comes from `pip-licenses` and the `uv.lock` files,',
    'npm data from the `package-lock.json` files. To regenerate: `node scripts/notices/generate.mjs`.',
    '',
    'AI runtimes and model weights are not dependencies: they are downloaded by the user, and their licenses are in [docs/licensing.md](docs/licensing.md).',
    '',
  ];
  if (violations.length > 0) {
    out.push('## Production licenses outside the policy', '', 'These need an ADR and an entry in `scripts/notices/policy.json`, or a replacement.', '');
    for (const v of violations) out.push(`- ${v}`);
    out.push('');
  }
  for (const c of components) {
    out.push(`## ${c.title}`, '', `Source: \`${c.source}\`.`, '', '### Production', '', table(c.prod), '### Development only', '', table(c.dev));
  }
  return { text: `${out.join('\n').replace(/\n+$/, '')}\n`, violations, problems };
}

function main() {
  const { text, violations, problems } = build();
  const target = join(ROOT, 'THIRD_PARTY_NOTICES.md');
  let failed = false;
  for (const p of problems) {
    console.error(`error: ${p}`);
    failed = true;
  }
  if (process.argv.includes('--check')) {
    let current = '';
    try {
      current = readFileSync(target, 'utf8');
    } catch {
      /* a missing file counts as stale */
    }
    if (current !== text) {
      console.error('error: THIRD_PARTY_NOTICES.md is stale; run node scripts/notices/generate.mjs');
      failed = true;
    }
  } else if (problems.length === 0) {
    writeFileSync(target, text);
    console.log('wrote THIRD_PARTY_NOTICES.md');
  }
  for (const v of violations) {
    console.error(`policy: ${v}`);
    failed = true;
  }
  process.exit(failed ? 1 : 0);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
