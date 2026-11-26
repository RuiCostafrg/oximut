import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import {
  attachBaselineEvidence,
  COVERAGE_TOO_LARGE,
  compactHits,
  fileTimings,
} from '../baseline-evidence.ts';
import { MAX_RESPONSE_BYTES } from '../protocol.mjs';

const root = '/project/test';
const job = { kind: 'baseline' };
const size = (value) => Buffer.byteLength(JSON.stringify(value));

function module(name, parts) {
  return {
    moduleId: `${root}/${name}.test.ts`,
    diagnostic: () => ({
      environmentSetupDuration: 0,
      prepareDuration: 0,
      collectDuration: 0,
      setupDuration: 0,
      duration: 0,
      ...parts,
    }),
  };
}

/** A suite of `files` files with `perFile` tests, and mutants that every test reaches. */
function suite(files, perFile, mutants, reached = () => true) {
  const tests = [];
  for (let file = 0; file < files; file++)
    for (let test = 0; test < perFile; test++)
      tests.push({
        // Vitest ids: a hash per file, then the test's position.
        id: `${(file * 7919 + 100003).toString(16)}_${file}_${test}`,
        file: `${root}/f${String(file).padStart(4, '0')}.test.ts`,
        name: `suite ${file} > case ${test} handles an ordinary example`,
      });
  tests.sort((a, b) => a.id.localeCompare(b.id));
  const hits = Array.from({ length: mutants }, (_, mutant) => ({
    mutantId: mutant.toString(16).padStart(64, '0'),
    tests: tests.filter((test, position) => reached(mutant, position)).map((test) => test.id).sort(),
    outsideTest: false,
  }));
  return { complete: true, tests, hits, reasons: [] };
}

const result = (coverage) => ({
  outcome: 'passed',
  tests: { passed: coverage.tests.length, failed: 0, skipped: 0 },
  errors: [],
  coverage,
});

test('file timings add up a module’s import, setup and tests, rounded up', () => {
  const timings = fileTimings([
    module('b', { collectDuration: 10.2, duration: 5 }),
    module('a', { environmentSetupDuration: 1, prepareDuration: 2, setupDuration: 3, collectDuration: 4, duration: 5.5 }),
    module('c', { duration: Number.NaN, collectDuration: -3 }),
    { moduleId: `${root}/broken.test.ts`, diagnostic: () => { throw new Error('no diagnostic'); } },
  ]);
  assert.deepEqual(timings, [
    { file: `${root}/a.test.ts`, durationMs: 16 },
    { file: `${root}/b.test.ts`, durationMs: 16 },
    { file: `${root}/c.test.ts`, durationMs: 0 },
  ]);
});

test('only baselines report timings and evidence', () => {
  const coverage = suite(2, 2, 2);
  const mutant = result(coverage);
  attachBaselineEvidence({ kind: 'mutant' }, [module('a', { duration: 1 })], mutant);
  assert.equal(mutant.fileTimings, undefined);
  const baseline = result(coverage);
  attachBaselineEvidence(job, [module('a', { duration: 1 })], baseline);
  assert.deepEqual(baseline.fileTimings, [{ file: `${root}/a.test.ts`, durationMs: 1 }]);
  // Coverage that fits is left exactly as collected.
  assert.equal(baseline.coverage, coverage);
});

test('hit lists become index runs over the sorted test inventory', () => {
  const coverage = suite(3, 4, 0);
  const at = (...positions) => positions.map((position) => coverage.tests[position].id);
  coverage.hits = [
    { mutantId: 'a', tests: at(0, 1, 2, 3, 8, 9), outsideTest: false },
    { mutantId: 'b', tests: [], outsideTest: true },
    { mutantId: 'c', tests: at(11, 5, 6), outsideTest: false },
    { mutantId: 'd', tests: ['not-collected'], outsideTest: false },
  ];
  const compact = compactHits(coverage);
  assert.deepEqual(compact.hits[0], { mutantId: 'a', tests: [], outsideTest: false, testRanges: [[0, 3], [8, 9]] });
  assert.deepEqual(compact.hits[1], { mutantId: 'b', tests: [], outsideTest: true, testRanges: [] });
  assert.deepEqual(compact.hits[2].testRanges, [[5, 6], [11, 11]]);
  // An id outside the inventory stays explicit for the CLI to reject.
  assert.deepEqual(compact.hits[3], coverage.hits[3]);
  assert.equal(compact.tests, coverage.tests);
});

test('a large suite’s coverage is compacted to fit the response limit, losslessly', () => {
  // 120 files of 30 tests and 400 mutants that every test reaches at load time:
  // 3,600 tests x 400 mutants of explicit ids is tens of megabytes.
  const coverage = suite(120, 30, 400);
  const explicit = size(result(coverage));
  assert.ok(explicit > 10 * MAX_RESPONSE_BYTES, `${explicit} bytes explicit`);
  const baseline = result(coverage);
  attachBaselineEvidence(job, [module('a', { duration: 5 })], baseline);
  assert.ok(size(baseline) < MAX_RESPONSE_BYTES, `${size(baseline)} bytes compact`);
  assert.equal(baseline.coverage.complete, true);
  assert.deepEqual(baseline.coverage.reasons, []);
  assert.equal(baseline.coverage.tests.length, 3600);
  // Expanding the runs gives back every mutant's test set.
  for (const [position, hit] of baseline.coverage.hits.entries()) {
    assert.equal(hit.mutantId, coverage.hits[position].mutantId);
    const expanded = hit.testRanges.flatMap(([first, last]) =>
      baseline.coverage.tests.slice(first, last + 1).map((test) => test.id),
    );
    assert.deepEqual(expanded.sort(), coverage.hits[position].tests);
  }
});

test('scattered hits that stay too large even as runs degrade to full-suite selection', () => {
  // Every mutant reached by every other test: runs cannot shrink it.
  const coverage = suite(300, 40, 300, (mutant, position) => position % 2 === 0);
  const baseline = result(coverage);
  baseline.coverage.reasons = ['source-reflection'];
  attachBaselineEvidence(job, [module('a', { duration: 5 })], baseline);
  assert.ok(size(baseline) < MAX_RESPONSE_BYTES);
  assert.equal(baseline.coverage.complete, false);
  assert.deepEqual(baseline.coverage.tests, []);
  assert.deepEqual(baseline.coverage.hits, []);
  assert.deepEqual(baseline.coverage.reasons, [COVERAGE_TOO_LARGE, 'source-reflection']);
  // Timings stay: the deadlines do not depend on selection.
  assert.equal(baseline.fileTimings.length, 1);
});

test('timings too large for a response are left out instead of failing it', () => {
  const modules = Array.from({ length: 2500 }, (_, index) =>
    module(`${'long-directory-name/'.repeat(4)}file-${index}`, { duration: 1 }),
  );
  const baseline = result(suite(1, 1, 1));
  attachBaselineEvidence(job, modules, baseline);
  assert.equal(baseline.fileTimings, undefined);
  assert.ok(size(baseline) < MAX_RESPONSE_BYTES);
});

test('coverage goes to a new private file in the directory the CLI offered, whatever its size', () => {
  const directory = mkdtempSync(join(tmpdir(), 'oximut-evidence-test-'));
  try {
    // A suite whose coverage cannot be made to fit a response at all.
    const coverage = suite(300, 40, 300, (mutant, position) => position % 2 === 0);
    const baseline = result(coverage);
    attachBaselineEvidence({ ...job, coverageDir: directory }, [module('a', { duration: 5 })], baseline);
    assert.equal(baseline.coverage, undefined);
    assert.ok(size(baseline) < MAX_RESPONSE_BYTES);
    assert.equal(baseline.fileTimings.length, 1);
    const written = join(directory, baseline.coverageFile);
    assert.deepEqual(JSON.parse(readFileSync(written, 'utf8')), coverage);
    // Owner-only: the CLI refuses a file anyone else could have written.
    assert.equal(statSync(written).mode & 0o077, 0);
    // Another baseline in the same directory never replaces it.
    const again = result(coverage);
    attachBaselineEvidence({ ...job, coverageDir: directory }, [], again);
    assert.notEqual(again.coverageFile, baseline.coverageFile);
    assert.equal(readdirSync(directory).length, 2);
    // Mutant jobs carry no evidence, directory or not.
    const mutant = result(coverage);
    attachBaselineEvidence({ kind: 'mutant', coverageDir: directory }, [], mutant);
    assert.equal(mutant.coverageFile, undefined);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('without a usable coverage directory coverage keeps to the response limit', () => {
  const coverage = suite(120, 30, 400);
  const missing = join(tmpdir(), 'oximut-evidence-test-missing', 'nowhere');
  for (const input of [job, { ...job, coverageDir: missing }]) {
    const baseline = result(coverage);
    attachBaselineEvidence(input, [], baseline);
    assert.equal(baseline.coverageFile, undefined);
    assert.ok(size(baseline) < MAX_RESPONSE_BYTES);
    assert.equal(baseline.coverage.complete, true);
  }
});
