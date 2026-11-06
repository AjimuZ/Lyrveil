// Runs the production `ng build` and fails on any build warning or on a network reference in the output.
//
// Usage: node tools/build-strict.mjs. Exit status 0 only when ng build exits 0, prints no warning, and the files in
// dist/lyrveil/browser load nothing from another origin: every url() in CSS and every src or href in HTML is
// relative, and every font the CSS names exists in the output.
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** Output folder of the browser build, relative to the frontend folder (angular.json outputPath plus browser/). */
export const OUTPUT = 'dist/lyrveil/browser';

// The builder marks each warning with "WARNING", as in "▲ [WARNING] bundle initial exceeded maximum budget".
const WARNING = /\bWARNING\b/;
const CSS_URL = /url\(\s*(['"]?)([^'")]+)\1\s*\)/gi;
const CSS_IMPORT = /@import\s+(['"])([^'"]+)\1/gi;
const HTML_REFERENCE = /\s(?:src|href|srcset|poster|data)\s*=\s*(['"])([^'"]*)\1/gi;
const FONT_FILE = /\.(?:woff2?|ttf|otf)(?:[?#].*)?$/i;

/** Returns the lines of build output that report a warning. */
export function findWarnings(output) {
  return output.split(/\r?\n/).filter((line) => WARNING.test(line));
}

/** True when a reference reaches another origin: a scheme other than data: or blob:, or a protocol-relative URL. */
export function isNetworkReference(reference) {
  const value = reference.trim();
  if (/^(?:data|blob):/i.test(value)) {
    return false;
  }
  return value.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(value);
}

/** Returns every URL a CSS text references through url() or @import, in source order. */
export function cssReferences(text) {
  return [...text.matchAll(CSS_URL), ...text.matchAll(CSS_IMPORT)]
    .sort((a, b) => a.index - b.index)
    .map((match) => match[2]);
}

/** Returns every URL an HTML text references through src, href, srcset, poster or data attributes. */
export function htmlReferences(text) {
  return [...text.matchAll(HTML_REFERENCE)].flatMap((match) => match[2].split(',').map((part) => part.trim().split(/\s+/)[0]));
}

/**
 * Checks the built files under output and returns a list of problems: a network reference in CSS or HTML, a font
 * referenced by CSS that is missing from the output, or a stylesheet that references no font at all, which would
 * mean the bundled fonts were left out of the build.
 */
export function checkOutput(output) {
  const problems = [];
  let fonts = 0;
  for (const entry of readdirSync(output, { recursive: true })) {
    const path = join(output, entry);
    const name = relative(output, path).split(sep).join('/');
    if (name.endsWith('.css')) {
      for (const reference of cssReferences(readFileSync(path, 'utf8'))) {
        if (isNetworkReference(reference)) {
          problems.push(`${name}: loads ${reference} from the network`);
        } else if (FONT_FILE.test(reference)) {
          fonts++;
          const file = join(dirname(path), reference.replace(/[?#].*$/, ''));
          if (!existsSync(file)) {
            problems.push(`${name}: font ${reference} is not in the build output`);
          }
        }
      }
    } else if (name.endsWith('.html')) {
      for (const reference of htmlReferences(readFileSync(path, 'utf8'))) {
        if (isNetworkReference(reference)) {
          problems.push(`${name}: loads ${reference} from the network`);
        }
      }
    }
  }
  if (fonts === 0) {
    problems.push('no stylesheet references a bundled font');
  }
  return problems;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const ng = join(root, 'node_modules', '@angular', 'cli', 'bin', 'ng.js');
  // Run through Node itself, without a shell; NG_FORCE_TTY=false keeps the output free of progress redraws.
  const run = spawnSync(process.execPath, [ng, 'build', '--configuration', 'production'], {
    cwd: root,
    encoding: 'utf8',
    env: { ...process.env, NG_CLI_ANALYTICS: 'false', NG_FORCE_TTY: 'false' },
  });
  const output = `${run.stdout ?? ''}${run.stderr ?? ''}`;
  process.stdout.write(output);

  const problems = [];
  if (run.error) {
    problems.push(`ng build could not start: ${run.error.message}`);
  } else if (run.status !== 0) {
    problems.push(`ng build exited with ${run.status}`);
  }
  for (const warning of findWarnings(output)) {
    problems.push(`build warning: ${warning.trim()}`);
  }
  if (problems.length === 0) {
    problems.push(...checkOutput(join(root, OUTPUT)));
  }
  for (const problem of problems) {
    console.error(problem);
  }
  console.log(`Strict build: ${problems.length} problem(s)`);
  process.exitCode = problems.length === 0 ? 0 : 1;
}
