import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import test from 'node:test';
import { promisify } from 'node:util';
import * as runtime from '../protocol.mjs';
import * as compatibility from '../protocol.ts';

const exec = promisify(execFile);
const baseline = {
  protocol: 1,
  runId: 'run',
  jobId: 'job',
  generation: 0,
  kind: 'baseline',
  root: '/project',
  config: '/project/vitest.config.ts',
  deadlineMs: 1,
  mutation: null,
};
const mutation = {
  id: 'mutation',
  file: '/project/source.ts',
  code: '',
  map: { version: 3, sources: ['source.ts'], mappings: '' },
};
const mutant = { ...baseline, kind: 'mutant', mutation };
const selection = {
  mode: 'files',
  files: ['/project/sample.test.ts'],
  selectedTests: 1,
  totalTests: 2,
  reasons: [],
};

test('protocol runtime and outer index load in a fresh process with TS stripping disabled', async () => {
  const { stdout } = await exec(process.execPath, [
    '--no-experimental-strip-types',
    '--input-type=module',
    '-e',
    `const protocol = await import(${JSON.stringify(new URL('../protocol.mjs', import.meta.url).href)});
     const adapter = await import(${JSON.stringify(new URL('../index.mjs', import.meta.url).href)});
     protocol.validateRequest(${JSON.stringify(baseline)});
     console.log(typeof adapter.openController);`,
  ]);
  assert.equal(stdout, 'function\n');
  // Control: the flag must actually prevent loading the retained TS API.
  await assert.rejects(
    exec(process.execPath, [
      '--no-experimental-strip-types',
      '--input-type=module',
      '-e',
      `await import(${JSON.stringify(new URL('../protocol.ts', import.meta.url).href)});`,
    ]),
    /ERR_UNKNOWN_FILE_EXTENSION/,
  );
});

test('TS compatibility exports use the same runtime implementation and byte limits', () => {
  assert.equal(compatibility.validateRequest, runtime.validateRequest);
  assert.equal(compatibility.envelope, runtime.envelope);
  assert.equal(compatibility.MAX_REQUEST_BYTES, runtime.MAX_REQUEST_BYTES);
  assert.equal(compatibility.MAX_RESPONSE_BYTES, runtime.MAX_RESPONSE_BYTES);
  assert.equal(runtime.MAX_REQUEST_BYTES, 8 * 1024 * 1024);
  assert.equal(runtime.MAX_RESPONSE_BYTES, 1024 * 1024);
});

test('validation accepts existing boundary, mutation, coverage and selection requests unchanged', () => {
  for (const value of [
    baseline,
    {
      ...baseline,
      runId: 'r'.repeat(1024),
      jobId: 'j'.repeat(1024),
      generation: Number.MAX_SAFE_INTEGER,
      deadlineMs: 2147483647,
    },
    mutant,
    { ...baseline, collectCoverage: [] },
    { ...baseline, collectCoverage: [mutation] },
    { ...mutant, selection },
    { ...mutant, selection: { ...selection, expectedSkipped: 0 } },
    { ...mutant, selection: { ...selection, expectedSkipped: 1 } },
    { ...mutant, selection: { ...selection, mode: 'full', files: [], totalTests: 1 } },
  ]) {
    const before = structuredClone(value);
    assert.equal(runtime.validateRequest(value), undefined);
    assert.deepEqual(value, before);
  }
});

test('validation preserves rejection order and exact diagnostic messages', () => {
  const cases = [];
  const add = (values, message) => {
    for (const value of values) cases.push([value, message]);
  };
  add([null, [], {}, { ...baseline, protocol: 2 }], 'Unsupported or missing protocol (expected 1)');
  for (const field of ['runId', 'jobId'])
    add(
      ['', 'x'.repeat(1025), 1, null].map((value) => ({ ...baseline, [field]: value })),
      'Invalid runId or jobId',
    );
  add(
    [-1, 0.5, Number.MAX_SAFE_INTEGER + 1, '0', null].map((generation) => ({
      ...baseline,
      generation,
    })),
    'Invalid generation',
  );
  add([{ ...baseline, kind: 'other' }], 'Invalid job kind');
  for (const field of ['root', 'config'])
    add(
      ['relative', '/nul\0path', null, 0].map((value) => ({ ...baseline, [field]: value })),
      `${field} must be an absolute path`,
    );
  add(
    [0, -1, 0.5, 2147483648, '1', null].map((deadlineMs) => ({ ...baseline, deadlineMs })),
    'Invalid deadlineMs',
  );
  add(
    [
      { ...baseline, mutation },
      { ...baseline, mutation: undefined },
    ],
    'Baseline mutation must be null',
  );
  add(
    [
      null,
      [],
      {},
      { ...mutation, id: '' },
      { ...mutation, code: null },
      { ...mutation, file: 'relative' },
      { ...mutation, file: '/project/nul\0file' },
      { ...mutation, map: [] },
    ].map((mutation) => ({ ...mutant, mutation })),
    'Invalid mutation',
  );
  add(
    ['/project', '/outside.ts', '/project/../escape.ts'].map((file) => ({
      ...mutant,
      mutation: { ...mutation, file },
    })),
    'Mutation file must be inside root',
  );
  add(
    [
      {},
      { ...mutation.map, version: 2 },
      { ...mutation.map, sources: [1] },
      { ...mutation.map, sources: null },
      { ...mutation.map, mappings: null },
    ].map((map) => ({ ...mutant, mutation: { ...mutation, map } })),
    'Invalid version 3 source map',
  );
  add(
    [
      { ...mutant, collectCoverage: [] },
      { ...baseline, collectCoverage: {} },
    ],
    'Coverage collection requires baseline probe transforms',
  );
  add([{ ...baseline, collectCoverage: [null] }], 'Invalid mutation');
  add(
    [{ ...baseline, collectCoverage: [{ ...mutation, file: '/outside.ts' }] }],
    'Mutation file must be inside root',
  );
  add([{ ...baseline, selection }], 'Invalid test selection');
  const selections = [null, {}, { ...selection, mode: 'none' }];
  for (const files of [
    [],
    ['/outside.ts'],
    ['/project'],
    ['relative'],
    ['/project/nul\0file'],
    [1],
    [selection.files[0], selection.files[0]],
  ])
    selections.push({ ...selection, files });
  for (const selectedTests of [0, -1, 0.5, '1', 3])
    selections.push({ ...selection, selectedTests });
  for (const totalTests of [0, 0.5, '2']) selections.push({ ...selection, totalTests });
  for (const expectedSkipped of [-1, 0.5, 2, '0'])
    selections.push({ ...selection, expectedSkipped });
  for (const reasons of [null, [1]]) selections.push({ ...selection, reasons });
  selections.push({ ...selection, mode: 'full' }, { ...selection, mode: 'full', files: [] });
  add(
    selections.map((selection) => ({ ...mutant, selection })),
    'Invalid test selection',
  );
  for (const [value, message] of cases)
    assert.throws(() => runtime.validateRequest(value), { name: 'Error', message });
});

test('diagnostic envelopes preserve identity fallbacks, result fields and error truncation', () => {
  const result = {
    outcome: 'failed',
    tests: { passed: 1, failed: 2, skipped: 3 },
    errors: Array.from({ length: 10 }, (_, index) => `${index}${'x'.repeat(3000)}`),
    coverage: { complete: false, tests: [], hits: [], reasons: ['incomplete'] },
  };
  const before = structuredClone(result);
  for (const value of [
    null,
    [],
    { runId: '', jobId: 'x'.repeat(1025), generation: -1 },
    { generation: '1' },
  ]) {
    assert.deepEqual(runtime.envelope(value, result), {
      protocol: 1,
      runId: '',
      jobId: '',
      generation: 0,
      ...result,
      errors: result.errors.slice(0, 8).map((error) => error.slice(0, 2048)),
    });
  }
  const response = runtime.envelope({ ...baseline, generation: Number.MAX_SAFE_INTEGER }, result);
  assert.equal(response.runId, baseline.runId);
  assert.equal(response.jobId, baseline.jobId);
  assert.equal(response.generation, Number.MAX_SAFE_INTEGER);
  assert.equal(response.errors.length, 8);
  assert.equal(response.errors[0].length, 2048);
  assert.deepEqual(result, before);
});
