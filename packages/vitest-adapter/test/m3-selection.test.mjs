import assert from 'node:assert/strict';
import { appendFileSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { attachBaselineEvidence } from '../baseline-evidence.ts';
import { MAX_RESPONSE_BYTES } from '../protocol.mjs';
import { SelectionSupport } from '../m3-selection.ts';

const root = '/project';
const id = (index) => index.toString(16).padStart(64, '0');

/** A Vitest stand-in with the configuration the support inspects, and modules of tests. */
function fakeVitest() {
  return {
    projects: [
      {
        config: {
          setupFiles: [],
          globalSetup: [],
          environment: 'node',
          server: { deps: { external: [] } },
          sequence: { shuffle: false },
        },
        vite: { config: { plugins: [] } },
      },
    ],
  };
}
function moduleOf(file, count) {
  return {
    moduleId: `${root}/${file}.test.ts`,
    children: {
      allTests: () =>
        Array.from({ length: count }, (_, index) => ({
          // Vitest ids: a hash per file, then the test's position.
          id: `${file}_0_${index}`,
          fullName: `suite ${file} > case ${index} handles an ordinary example`,
          options: {},
          result: () => ({ state: 'passed' }),
        })),
    },
  };
}
function collecting(mutants) {
  return {
    collectCoverage: mutants.map((mutant) => ({
      id: 'probes',
      file: `${root}/source-${mutant}.ts`,
      code: `export const f = () => (globalThis.__oximut_m3_hit__?.("${id(mutant)}"), 1);`,
      map: {},
    })),
  };
}
/** Plays what one test worker's probe wrote: its own stream of lines. */
function stream(directory, name, lines) {
  appendFileSync(
    join(directory, `${name}.jsonl`),
    `${lines.map((line) => JSON.stringify(line)).join('\n')}\n`,
  );
}
function begin(request) {
  const support = new SelectionSupport();
  const vitest = fakeVitest();
  support.begin(vitest, request);
  for (const probe of request.collectCoverage) support.observed.add(probe.file);
  return { support, vitest, directory: process.env.OXIMUT_M3_COVERAGE };
}
const expandRuns = (coverage, hit) =>
  hit.testRanges.flatMap(([first, last]) =>
    coverage.tests.slice(first, last + 1).map((candidate) => candidate.id),
  );

test('hits are read from batched streams, with their test and outside-test attribution', () => {
  const request = collecting([0, 1, 2, 3]);
  const { support, vitest, directory } = begin(request);
  try {
    const modules = [moduleOf('a', 2), moduleOf('b', 2)];
    // Two workers (one per file). Mutant 3 is never hit; mutant 2 only while a file loads.
    stream(directory, '1-0', [
      { ready: true },
      { ids: [id(2), id(0)] },
      { outside: [0] },
      { test: 'a_0_1', hits: [1] },
    ]);
    stream(directory, '2-0', [
      { ready: true },
      { ids: [id(0), id(1)] },
      { test: 'b_0_0', hits: [0, 1] },
      { test: 'b_0_1', hits: [0] },
    ]);
    const result = { outcome: 'passed' };
    const coverage = support.finish(vitest, request, modules, result);
    assert.deepEqual(coverage.reasons, []);
    assert.equal(coverage.complete, true);
    assert.deepEqual(
      coverage.tests.map((candidate) => candidate.id),
      ['a_0_0', 'a_0_1', 'b_0_0', 'b_0_1'],
    );
    const hits = new Map(coverage.hits.map((hit) => [hit.mutantId, hit]));
    assert.deepEqual(hits.get(id(0)).tests, ['a_0_1', 'b_0_0', 'b_0_1']);
    assert.deepEqual(hits.get(id(1)).tests, ['b_0_0']);
    assert.equal(hits.get(id(2)).outsideTest, true);
    assert.deepEqual(hits.get(id(2)).tests, []);
    // Never hit: an explicit empty entry, not a missing record.
    assert.deepEqual(hits.get(id(3)), { mutantId: id(3), tests: [], outsideTest: false });
  } finally {
    support.close();
  }
});

test('records that name nothing known make attribution incomplete', () => {
  for (const [lines, reason] of [
    [[{ ids: [id(9)] }], 'unknown-probe'],
    [[{ ids: [id(0)] }, { test: 'nope', hits: [0] }], 'unknown-test-identity'],
    [[{ test: 'a_0_0', hits: [4] }], 'missing-or-invalid-probe-records'],
    [[{ surprise: 1 }], 'missing-or-invalid-probe-records'],
    [[{ incomplete: 'source-reflection' }], 'source-reflection'],
  ]) {
    const request = collecting([0]);
    const { support, vitest, directory } = begin(request);
    try {
      stream(directory, '1-0', [{ ready: true }, ...lines]);
      const coverage = support.finish(vitest, request, [moduleOf('a', 1)], { outcome: 'passed' });
      assert.equal(coverage.complete, false, reason);
      assert.deepEqual(coverage.reasons, [reason]);
    } finally {
      support.close();
    }
  }
});

test('a suite of 25,000 tests keeps its hits as runs and its coverage goes to a file', () => {
  const files = 500;
  const perFile = 50;
  const mutants = Array.from({ length: 40 }, (_, index) => index);
  const request = { ...collecting(mutants), kind: 'baseline' };
  const { support, vitest, directory } = begin(request);
  const outputs = mkdtempSync(join(tmpdir(), 'oximut-coverage-test-'));
  try {
    const modules = Array.from({ length: files }, (_, file) =>
      moduleOf(`f${String(file).padStart(4, '0')}`, perFile),
    );
    // Each file's worker: mutant 0 is hit by every test (it loads with a shared module),
    // mutant 1 only by the first test of file 7, the others by nothing.
    for (let file = 0; file < files; file++) {
      const name = String(file).padStart(4, '0');
      const lines = [{ ready: true }, { ids: [id(0), id(1)] }];
      for (let index = 0; index < perFile; index++) {
        const hits = file === 7 && index === 0 ? [0, 1] : [0];
        lines.push({ test: `f${name}_0_${index}`, hits });
      }
      stream(directory, `${file}-0`, lines);
    }
    const result = {
      outcome: 'passed',
      tests: { passed: files * perFile, failed: 0, skipped: 0 },
      errors: [],
    };
    const coverage = support.finish(vitest, request, modules, result);
    assert.equal(coverage.complete, true, JSON.stringify(coverage.reasons));
    assert.equal(coverage.tests.length, 25_000);
    const hits = new Map(coverage.hits.map((hit) => [hit.mutantId, hit]));
    // Past the explicit limit, hits are index runs: a few of them however many tests.
    assert.deepEqual(hits.get(id(0)).tests, []);
    assert.deepEqual(hits.get(id(0)).testRanges, [[0, 24_999]]);
    assert.deepEqual(expandRuns(coverage, hits.get(id(1))), ['f0007_0_0']);
    assert.equal(coverage.tests[hits.get(id(1)).testRanges[0][0]].file, `${root}/f0007.test.ts`);
    assert.deepEqual(hits.get(id(2)).testRanges ?? [], []);
    assert.equal(hits.size, 40);

    // The payload is far past a response: it travels as a file the CLI is told to read.
    assert.ok(Buffer.byteLength(JSON.stringify(coverage)) > 2 * MAX_RESPONSE_BYTES);
    const response = { ...result, coverage };
    attachBaselineEvidence({ kind: 'baseline', coverageDir: outputs }, modules, response);
    assert.equal(response.coverage, undefined);
    assert.match(response.coverageFile, /^coverage-\d+-\d+\.json$/);
    assert.ok(Buffer.byteLength(JSON.stringify(response)) < MAX_RESPONSE_BYTES);
    assert.deepEqual(readdirSync(outputs), [response.coverageFile]);
    assert.deepEqual(
      JSON.parse(readFileSync(join(outputs, response.coverageFile), 'utf8')),
      coverage,
    );
  } finally {
    support.close();
    rmSync(outputs, { recursive: true, force: true });
  }
});
