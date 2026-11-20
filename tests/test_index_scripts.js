const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('index inline scripts parse without syntax errors', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const scripts = [...html.matchAll(/<script(?:\\s[^>]*)?>([\\s\\S]*?)<\\/script>/gi)]
    .map(match => match[1].trim())
    .filter(Boolean);
  assert.ok(scripts.length > 0, 'expected at least one inline script');
  for (const [index, source] of scripts.entries()) {
    assert.doesNotThrow(() => new Function(source), `inline script ${index + 1} should parse`);
  }
});
