// Generates the API types from the backend's OpenAPI document, and fails when the committed ones are out of date.
//
// Usage: node tools/api-types.mjs generate   writes src/app/core/api/generated from ../backend/openapi.json.
//        node tools/api-types.mjs check      exit status 0 when the committed types equal a fresh generation, else 1.
// The types come from the committed document, not from a running backend, so the lint needs no Docker. The backend's
// own test (backend/tests/unit/test_openapi_document.py) keeps that document equal to the application's.
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** The OpenAPI document written by the backend, relative to the frontend folder. */
export const DOCUMENT = '../backend/openapi.json';
/** Where the generated types are committed, relative to the frontend folder. */
export const GENERATED_DIR = 'src/app/core/api/generated';

/**
 * Returns the generator configuration for the document under root, writing to the folder `output`. Only the
 * TypeScript types are generated: the HTTP calls are written by hand in src/app/core/api, on Angular's HttpClient.
 * Logging is off, so a clean run prints nothing and a crash leaves no log file behind.
 */
export function generatorConfig(root, output) {
  return {
    input: join(root, DOCUMENT),
    output: { path: output },
    plugins: ['@hey-api/typescript'],
    logs: { level: 'silent', file: false },
    interactive: false,
  };
}

/** Generates the types for the document under root into the folder `output`, replacing what it holds. */
export async function generate(root, output) {
  const { createClient } = await import('@hey-api/openapi-ts');
  await createClient(generatorConfig(root, output));
}

function listFiles(folder) {
  return readdirSync(folder, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => relative(folder, join(entry.parentPath, entry.name)).split(sep).join('/'))
    .sort();
}

// A Windows checkout may hold CRLF while the generator writes LF; only the content counts.
function readText(folder, name) {
  return readFileSync(join(folder, ...name.split('/')), 'utf8').replace(/\r\n/g, '\n');
}

/**
 * Returns one line per difference between the folder `committed` and the folder `fresh`: a file only in one of them,
 * or a file whose text differs. An empty list means the two hold the same files with the same text.
 */
export function compareFolders(committed, fresh) {
  const before = listFiles(committed);
  const after = listFiles(fresh);
  const differences = [];
  for (const name of before.filter((file) => !after.includes(file))) {
    differences.push(`${name}: committed, but no longer generated`);
  }
  for (const name of after.filter((file) => !before.includes(file))) {
    differences.push(`${name}: generated, but not committed`);
  }
  for (const name of before.filter((file) => after.includes(file))) {
    if (readText(committed, name) !== readText(fresh, name)) {
      differences.push(`${name}: differs from a fresh generation`);
    }
  }
  return differences;
}

/** Returns the differences between the committed types under root and a fresh generation. */
export async function checkApiTypes(root) {
  const scratch = mkdtempSync(join(tmpdir(), 'lyrveil-api-types-'));
  try {
    await generate(root, scratch);
    return compareFolders(join(root, GENERATED_DIR), scratch);
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const command = process.argv[2];
  if (command === 'generate') {
    await generate(root, join(root, GENERATED_DIR));
    console.log(`API types: generated ${GENERATED_DIR} from ${DOCUMENT}`);
  } else if (command === 'check') {
    const differences = await checkApiTypes(root);
    for (const difference of differences) {
      console.error(`${GENERATED_DIR}/${difference}`);
    }
    console.log(`API types: ${differences.length} difference(s) from ${DOCUMENT}`);
    if (differences.length > 0) {
      console.error('Run "npm run api:generate" and commit the result.');
    }
    process.exitCode = differences.length === 0 ? 0 : 1;
  } else {
    console.error('usage: node tools/api-types.mjs generate|check');
    process.exitCode = 2;
  }
}
