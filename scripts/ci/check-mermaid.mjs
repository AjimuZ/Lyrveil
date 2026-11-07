// Parses every Mermaid diagram in the Git-tracked Markdown and fails on a syntax error (docs/testing.md, Continuous
// integration). Each diagram is rendered by the Mermaid command line, the parser GitHub's own renderer shares, so a
// diagram that passes here draws.
//
// Usage: node scripts/ci/check-mermaid.mjs
// Environment: MERMAID_CLI is the mmdc executable (default "mmdc"); PUPPETEER_CONFIG is the Puppeteer settings file
// passed to it (the CI workflow disables the browser sandbox there, which hosted runners require).
// Exit status: 0 when at least one diagram was found and all parse, 1 on any syntax error, any failure to run the
// parser, or when no diagram was found, because a check that parsed nothing must not read as a pass.

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

/**
 * Returns the Mermaid diagrams of one Markdown text as { line, source }, where line is the 1-based line of the
 * opening fence. A diagram is a fenced block whose info string is exactly "mermaid", fenced by three or more backticks
 * or tildes and closed by the same character at least as long. An unclosed fence runs to the end of the text, as in
 * CommonMark, so a missing closing fence is parsed, and fails, rather than skipped.
 */
export function extractDiagrams(markdown) {
  const lines = markdown.split(/\r?\n/);
  const diagrams = [];
  let open = null;
  for (let index = 0; index < lines.length; index += 1) {
    const text = lines[index];
    if (open === null) {
      const start = /^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)\s*$/.exec(text);
      if (start) {
        open = { fence: start[1], language: start[2], line: index + 1, body: [] };
      }
      continue;
    }
    const end = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(text);
    if (end && end[1][0] === open.fence[0] && end[1].length >= open.fence.length) {
      if (open.language === 'mermaid') diagrams.push({ line: open.line, source: open.body.join('\n') });
      open = null;
      continue;
    }
    open.body.push(text);
  }
  if (open !== null && open.language === 'mermaid') diagrams.push({ line: open.line, source: open.body.join('\n') });
  return diagrams;
}

function trackedMarkdown() {
  const output = execFileSync('git', ['ls-files', '-z', '--', '*.md'], { encoding: 'utf8' });
  return output.split('\0').filter((name) => name.length > 0);
}

function main() {
  const cli = process.env.MERMAID_CLI || 'mmdc';
  const config = process.env.PUPPETEER_CONFIG;
  const directory = mkdtempSync(join(tmpdir(), 'lyrveil-mermaid-'));
  let count = 0;
  const failures = [];
  try {
    for (const file of trackedMarkdown()) {
      for (const { line, source } of extractDiagrams(readFileSync(file, 'utf8'))) {
        count += 1;
        const input = join(directory, `diagram-${count}.mmd`);
        writeFileSync(input, source, 'utf8');
        const arguments_ = ['--input', input, '--output', join(directory, `diagram-${count}.svg`), '--quiet'];
        if (config) arguments_.push('--puppeteerConfigFile', config);
        try {
          execFileSync(cli, arguments_, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
          console.log(`  [ok  ] ${file}:${line}`);
        } catch (error) {
          const detail = `${error.stderr ?? ''}${error.stdout ?? ''}`.trim() || String(error.message);
          failures.push(`${file}:${line}\n${detail}`);
          console.log(`  [FAIL] ${file}:${line}`);
        }
      }
    }
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
  console.log(`Mermaid diagrams: ${count} checked, ${failures.length} failed`);
  for (const failure of failures) console.log(`\n${failure}`);
  if (count === 0) {
    console.log('No Mermaid diagram was found in the tracked Markdown, so nothing was parsed.');
    return 1;
  }
  return failures.length === 0 ? 0 : 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main();
}
