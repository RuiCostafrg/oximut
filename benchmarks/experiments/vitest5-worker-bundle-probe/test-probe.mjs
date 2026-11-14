import assert from 'node:assert/strict';
import test from 'node:test';
import { checkPins, preserveURL } from './probe.mjs';

test('reject unsupported metadata and resolution before bundle execution', async () => {
  for (const code of ['import.meta.dirname', 'import.meta.resolve("x")', 'const x = import.meta', 'import.meta["url"]']) {
    await assert.rejects(preserveURL(code, '/tmp/origin.mjs'), /Unsupported metadata/);
  }
});
test('reject computed imports and CommonJS require', async () => {
  await assert.rejects(preserveURL('import(name)', '/tmp/origin.mjs'), /Unsupported computed import/);
  await assert.rejects(preserveURL('require("x")', '/tmp/origin.mjs'), /Unsupported require/);
});
test('only actual URL syntax changes, and source contents survive in map', async () => {
  const code = 'const x = import.meta.url; const text = "import.meta.url"; import("./a.mjs")';
  const result = await preserveURL(code, '/tmp/origin.mjs');
  assert.equal(result.code, 'const x = "file:///tmp/origin.mjs"; const text = "import.meta.url"; import("./a.mjs")');
  assert.equal(result.sites.length, 1);
  assert.deepEqual(result.map.sourcesContent, [code]);
});
test('version mismatch rejects', () => {
  assert.throws(() => checkPins({ vitest: '4.1.12' }), /Unsupported package versions/);
  assert.throws(() => checkPins({}, 'v26.3.0'), /Unsupported Node/);
});
