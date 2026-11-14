import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile, mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { auditTopology, checkVersions, pins, transformPrivate, resolveOriginal } from './build.mjs';
import { inventory } from './fixtures.mjs';
import { validateReport } from './probe.mjs';

test('exact versions fail loudly', () => {
  checkVersions(pins);
  assert.throws(() => checkVersions({ ...pins, vite: '8.3.4' }), /Unsupported dependencies/);
  assert.throws(() => checkVersions(pins, 'v26.3.2'), /Unsupported Node/);
});
test('resolution really uses original importer, including conditional package exports', async t => {
  const root = await mkdtemp(join(tmpdir(), 'private-importer-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'a/node_modules/identity'), { recursive: true });
  await mkdir(join(root, 'b/node_modules/identity'), { recursive: true });
  for (const side of ['a','b']) await writeFile(join(root, side, 'node_modules/identity/package.json'), JSON.stringify({ type: 'module', exports: { import: './esm.js', require: './cjs.cjs' } }));
  assert.equal(resolveOriginal('./local.js', join(root, 'a/source.js')), pathToFileURL(join(root, 'a/local.js')).href);
  for (const side of ['a','b']) assert.equal(resolveOriginal('identity', join(root, side, 'source.js')), pathToFileURL(join(root, side, 'node_modules/identity/esm.js')).href);
});
test('metadata, computed imports and context syntax reject', () => {
  for (const code of ['import.meta.resolve("a")', 'import.meta.dirname', 'const m = import.meta', 'import.meta["url"]', 'import(target)', 'import("a", {with: {type:"json"}})', 'require("a")', 'eval("x")', 'Function("x")', 'const p = __dirname']) {
    assert.throws(() => transformPrivate(code, '/x/chunks/base.Cof-UHJf.js'), /Unsupported/);
  }
  assert.throws(() => transformPrivate('import.meta.url', '/x/other.js'), /Unsupported metadata/);
});
test('audited URL rewrite has precise original source map; string syntax is untouched', () => {
  const source = 'const literal = "import.meta.url"; const url = import.meta.url; import("./native.js")';
  const changed = transformPrivate(source, '/x/chunks/base.Cof-UHJf.js');
  assert.equal(changed.edits.length, 1);
  assert.ok(changed.code.includes('"file:///x/chunks/base.Cof-UHJf.js"'));
  assert.ok(changed.code.includes('"import.meta.url"'));
  assert.deepEqual(changed.map.sourcesContent, [source]);
});
test('complete shared/API intersection stays native and has no native ingress into allowlist', async () => {
  const topology = await auditTopology();
  assert.equal(topology.shared.length, 15);
  assert.equal(topology.selected.length, 6);
  assert.ok(topology.shared.some(id => id.endsWith('/run.CjdgFZiW.js')));
  topology.shared.forEach(id => assert.ok(!topology.selected.includes(id)));
  const approved = JSON.parse(await readFile(new URL('./audited-inputs.json', import.meta.url), 'utf8'));
  assert.equal(approved.filter(row => row.inline).length, 6);
});
test('oracle requires exact selector outcomes, inventory and failures; A/B cannot be swapped', () => {
  const report = { reason:'passed', errors:[], tests:inventory.map(name => ({name,state:'passed'})), testErrors:[] };
  validateReport(report, 'sequence', 'inactive');
  assert.throws(() => validateReport(report, 'sequence', 'A'), /Selector-specific outcome/);
  const missing = structuredClone(report); missing.tests.pop();
  assert.throws(() => validateReport(missing, 'sequence', 'inactive'), /Inventory diverged/);
  const stray = structuredClone(report); stray.errors.push({message:'unhandled'});
  assert.throws(() => validateReport(stray, 'sequence', 'inactive'), /Unexpected module/);
  const active = structuredClone(report);
  active.reason = 'failed'; active.tests[0].state = 'failed';
  active.testErrors = [{name:'arithmetic add',message:'expected 4 to be 3'}];
  validateReport(active, 'sequence', 'A');
  assert.throws(() => validateReport(active, 'sequence', 'B'), /Selector-specific outcome/);
});
