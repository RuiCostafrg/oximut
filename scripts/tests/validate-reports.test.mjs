import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const validator = new URL('../validate-reports.mjs', import.meta.url);
const sample = JSON.parse(await readFile(new URL('./fixtures/report.json', import.meta.url), 'utf8'));
const run = (...inputs) => spawnSync(process.execPath, [validator.pathname, ...inputs], { encoding: 'utf8' });

test('validates an archived emitted report through the CLI', () => {
  const result = run(new URL('./fixtures/report.json', import.meta.url).pathname);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Validated 1 report/);
});

test('allows an incomplete invocation report', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'oximut-schema-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const report = structuredClone(sample);
  report.summary.complete = false;
  report.baseline.outcome = 'resource-exhausted';
  report.baseline.errors = ['Insufficient memory budget'];
  report.mutants = [];
  report.summary.total = 0;
  report.summary.killed = 0;
  report.summary.survived = 0;
  report.summary.scoreDenominator = 0;
  report.summary.score = null;
  const file = join(directory, 'incomplete-report.json');
  await writeFile(file, JSON.stringify(report));
  const result = run(file);
  assert.equal(result.status, 0, result.stderr);
});

test('rejects missing fields, wrong field types, malformed JSON, and list output', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'oximut-schema-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const missing = structuredClone(sample);
  delete missing.cache;
  const wrongType = structuredClone(sample);
  wrongType.summary.complete = 'true';
  for (const [name, contents, message] of [
    ['missing.json', JSON.stringify(missing), /cache/],
    ['wrong-type.json', JSON.stringify(wrongType), /boolean/],
    ['malformed.json', '{', /cannot read JSON/],
    ['list.json', JSON.stringify({ schemaVersion: 1, mutants: [], diagnostics: [] }), /required/],
  ]) {
    const file = join(directory, name);
    await writeFile(file, contents);
    const result = run(file);
    assert.equal(result.status, 1, name);
    assert.ok(result.stderr.includes(file), result.stderr);
    assert.match(result.stderr, message);
  }
});

test('fails for no input, missing paths, and each empty directory', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'oximut-schema-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  assert.equal(run().status, 1);
  assert.equal(run(join(directory, 'missing.json')).status, 1);
  await writeFile(join(directory, 'metadata.json'), '{}');
  assert.match(run(directory).stderr, /no matching report files/);
  const empty = join(directory, 'empty');
  await mkdir(empty);
  assert.equal(run(new URL('./fixtures/report.json', import.meta.url).pathname, empty).status, 1);
});

test('directory traversal does not skip malformed matching reports', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'oximut-schema-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  await mkdir(join(directory, 'nested'));
  await writeFile(join(directory, 'report.json'), JSON.stringify(sample));
  await writeFile(join(directory, 'metadata.json'), '{');
  assert.equal(run(directory).status, 0);
  await writeFile(join(directory, 'nested', 'incomplete-report.json'), '{');
  assert.equal(run(directory).status, 1);
});
