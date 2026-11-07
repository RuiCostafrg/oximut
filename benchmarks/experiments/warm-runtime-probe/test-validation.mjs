import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { validateBatch, validateCase, validateControllerStatus } from './validation.mjs';

const root = new URL('../../results/2026-11-05-warm-runtime-probe/parent-reviewed/', import.meta.url);
const cases = ['stock','raw-warm', ...['module-only','env-only','fs-only','host-dirty'].flatMap(mode => [`guard-${mode}`, `fork-${mode}`])];
const evidence = new Map(await Promise.all(cases.map(async name => [name,
  (await readFile(new URL(`${name}/results.jsonl`, root), 'utf8')).trim().split('\n').map(JSON.parse)])));
const copy = name => structuredClone(evidence.get(name));

test('all retained stock, warm, guarded and fork observations satisfy the declared matrix', () => {
  for (const [name, rows] of evidence) validateBatch(rows, name);
});

test('negative controls need the actual environment and builtin leaks, not unrelated failures', () => {
  for (const name of ['stock','raw-warm']) for (const field of ['env','builtin']) {
    const row = copy(name).find(r => !r.fresh);
    row.events[1][field] = null;
    assert.throws(() => validateCase(row), /observed .* state/);
  }
});

test('each stock negative control must exhibit the module/global/timer leaks it claims', () => {
  for (const field of ['local','cjs','esm','global','fakeClock']) {
    const row = copy('stock').find(r => !r.fresh);
    row.events[1][field] = row.events[0][field];
    assert.throws(() => validateCase(row), /observed .* state/);
  }
});

test('exit status agrees with test failures in both directions', () => {
  const failed = [{ counts:{ passed:14, failed:2 } }];
  const passed = [{ counts:{ passed:16, failed:0 } }];
  validateControllerStatus({ code:1, signal:null }, failed);
  validateControllerStatus({ code:0, signal:null }, passed);
  assert.throws(() => validateControllerStatus({ code:0, signal:null }, failed), /exit code/);
  assert.throws(() => validateControllerStatus({ code:1, signal:null }, passed), /exit code/);
  assert.throws(() => validateControllerStatus({ code:null, signal:'SIGTERM' }, passed), /normally/);
});

test('stock separate calls cannot silently reuse a worker with fresh realms', () => {
  for (const row of copy('stock').filter(r => r.schedule === 'separate')) {
    row.events[1].pid = row.events[0].pid;
    row.events[1].threadId = row.events[0].threadId;
    row.reusedWorker = true;
    assert.throws(() => validateCase(row), /worker identity relationship/);
  }
});

test('persisted reuse flags cannot contradict independent identities', () => {
  const row = copy('stock')[0];
  row.reusedWorker = !row.reusedWorker;
  assert.throws(() => validateCase(row));
});

test('matrix reconciliation rejects missing and repeated cases', () => {
  const rows = copy('stock');
  assert.throws(() => validateBatch(rows.slice(1), 'stock'), /scenario inventory/);
  rows[0] = rows[1];
  assert.throws(() => validateBatch(rows, 'stock'), /scenario inventory/);
});

test('clean assertion counts cannot hide contamination or a mismatched guard identity', () => {
  const rows = copy('guard-host-dirty');
  rows[0].events[1].env = 'dirty';
  assert.throws(() => validateCase(rows[0]), /observed env state/);
  const row = copy('guard-host-dirty')[0];
  row.guard[1].threadId = row.guard[0].threadId;
  assert.throws(() => validateCase(row));
});
