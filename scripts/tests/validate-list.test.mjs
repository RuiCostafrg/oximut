import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { validateList } from '../validate-list.mjs';

const position = { offset: 0, line: 1, column: 1 };
const sample = {
  schemaVersion: 1,
  mutants: [{ id: 'opaque', file: 'source.ts', operator: 'boolean', original: 'true',
    replacement: 'false', location: { start: position, end: { ...position, offset: 4, column: 5 } },
    code: 'false', map: { version: 3, sources: ['source.ts'], mappings: '' } }],
  diagnostics: [],
};

test('list schema accepts empty and populated inventories and JSON source maps', () => {
  validateList(sample);
  validateList({ schemaVersion: 1, mutants: [], diagnostics: ['unsupported context'] });
  validateList({ ...sample, mutants: [{ ...sample.mutants[0], map: null }] });
});

test('list schema accepts optional extensions at every object level', () => {
  const listing = structuredClone(sample);
  listing.futureEnvelope = {};
  listing.mutants[0].futureMutant = true;
  listing.mutants[0].location.futureLocation = [];
  listing.mutants[0].location.start.futurePosition = 'optional';
  validateList(listing);
});

test('list schema rejects wrong versions, missing fields, invalid positions, and report envelopes', () => {
  const cases = [
    { ...sample, schemaVersion: 2 },
    { ...sample, diagnostics: [42] },
    ...['runId', 'baseline', 'summary', 'cache', 'resources'].map(field => ({ ...sample, [field]: null })),
    { schemaVersion: 1, mutants: [], summary: {} },
  ];
  for (const field of Object.keys(sample.mutants[0])) {
    const mutant = structuredClone(sample.mutants[0]);
    delete mutant[field];
    cases.push({ ...sample, mutants: [mutant] });
  }
  for (const [field, value] of [['offset', -1], ['line', 0], ['column', 1.5]]) {
    const listing = structuredClone(sample);
    listing.mutants[0].location.start[field] = value;
    cases.push(listing);
  }
  for (const listing of cases) assert.throws(() => validateList(listing, 'candidate'), /candidate:/);
});

test('list validator CLI checks every explicit file and reports malformed/missing inputs', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'oximut-list-schema-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const good = join(directory, 'list.json');
  const bad = join(directory, 'bad.json');
  await writeFile(good, JSON.stringify(sample));
  await writeFile(bad, '{');
  const run = (...inputs) => spawnSync(process.execPath,
    [new URL('../validate-list.mjs', import.meta.url).pathname, ...inputs], { encoding: 'utf8' });
  assert.equal(run(good).status, 0);
  for (const args of [[], [good, bad], [join(directory, 'missing.json')], [directory]]) {
    const result = run(...args);
    assert.equal(result.status, 1);
    if (args.length) assert.ok(result.stderr.includes(args.at(-1)), result.stderr);
  }
});
