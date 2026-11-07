// Tests the extraction of Mermaid diagrams from Markdown, the part of check-mermaid.mjs that needs no browser.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { extractDiagrams } from './check-mermaid.mjs';

test('finds a diagram and the line of its opening fence', () => {
  const text = ['# Title', '', '```mermaid', 'flowchart LR', '  A --> B', '```', ''].join('\n');
  assert.deepEqual(extractDiagrams(text), [{ line: 3, source: 'flowchart LR\n  A --> B' }]);
});

test('ignores other languages and mermaid text inside them', () => {
  const text = ['```text', '```mermaid', 'not a diagram', '```', '', '```json', '{}', '```'].join('\n');
  assert.deepEqual(extractDiagrams(text), []);
});

test('a longer outer fence keeps an inner fence as content', () => {
  const text = ['````mermaid', 'flowchart LR', '```', 'A --> B', '````'].join('\n');
  assert.deepEqual(extractDiagrams(text), [{ line: 1, source: 'flowchart LR\n```\nA --> B' }]);
});

test('reads tilde fences and Windows line endings', () => {
  const text = '~~~mermaid\r\nsequenceDiagram\r\n  A->>B: hi\r\n~~~\r\n';
  assert.deepEqual(extractDiagrams(text), [{ line: 1, source: 'sequenceDiagram\n  A->>B: hi' }]);
});

test('an unclosed fence still yields its diagram, so the parser can reject it', () => {
  const text = ['```mermaid', 'flowchart LR', '  A -->'].join('\n');
  assert.deepEqual(extractDiagrams(text), [{ line: 1, source: 'flowchart LR\n  A -->' }]);
});

test('finds several diagrams in one file', () => {
  const text = ['```mermaid', 'a', '```', 'text', '```mermaid', 'b', '```'].join('\n');
  assert.deepEqual(
    extractDiagrams(text).map((diagram) => diagram.line),
    [1, 5],
  );
});
