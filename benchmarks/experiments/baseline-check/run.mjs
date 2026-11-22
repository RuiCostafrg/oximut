// Fault-injection check for Oximut's original baseline. Each case is a tiny
// Vitest project whose tests observe instrumentation in a different way. Every
// case runs twice: with the default original + instrumented baselines, and with
// OXIMUT_EXPERIMENTAL_SKIP_ORIGINAL_BASELINE=1 (instrumented baseline only).
// A run DETECTS the case when it refuses to report a complete mutation result.
//
//   node benchmarks/experiments/baseline-check/run.mjs <oximut-binary> [out.json]
import { execFileSync, spawnSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const binary = resolve(process.argv[2]);
const output = process.argv[3] ? resolve(process.argv[3]) : undefined;
const repo = resolve(import.meta.dirname, '../../..');

const source = `export function clamp(value: number, low: number, high: number): number {
  if (value < low) return low;
  if (value > high) return high;
  return value;
}

export function checked(value: number): number {
  if (value < 0) throw new RangeError('negative');
  return value * 2;
}
`;
const head = `import { expect, it } from 'vitest';
import { checked, clamp } from '../src/math';
`;
const killing = `
it('clamps both ends', () => {
  expect(clamp(5, 0, 3)).toBe(3);
  expect(clamp(-1, 0, 3)).toBe(0);
  expect(clamp(3, 0, 3)).toBe(3);
  expect(clamp(0, 0, 3)).toBe(0);
});
`;
// The extra test is the only one that checks \`checked\`; dropping it changes the score.
const checkedTest = `it('doubles non-negative values', () => {
  expect(checked(2)).toBe(4);
  expect(() => checked(-1)).toThrow(RangeError);
  expect(checked(0)).toBe(0);
});
`;
const instrumented = `clamp.toString().includes('OXIMUT_ACTIVE_MUTANT')`;

const cases = [
  {name: 'control', simulates: 'no sensitivity', realism: 'baseline',
    test: killing + checkedTest},
  {name: 'source-text-fails', realism: 'plausible',
    simulates: 'a test inspects function source (purity or serialisation checks) and fails under instrumentation',
    test: killing + checkedTest + `it('clamp is pure', () => {
  expect(clamp.toString()).not.toContain('process.env');
});
`},
  {name: 'test-skipped-under-instrumentation', realism: 'contrived trigger, real hazard',
    simulates: 'a test that only exists on original code (skipIf on code shape); suite still passes',
    test: killing + `it.skipIf(${instrumented})('doubles non-negative values', () => {
  expect(checked(2)).toBe(4);
  expect(() => checked(-1)).toThrow(RangeError);
  expect(checked(0)).toBe(0);
});
`},
  {name: 'test-added-under-instrumentation', realism: 'contrived',
    simulates: 'an extra passing test registered only when code is instrumented',
    test: killing + checkedTest + `if (${instrumented}) it('extra instrumented-only test', () => {
  expect(clamp(1, 0, 3)).toBe(1);
});
`},
  {name: 'masked-real-failure', realism: 'contrived',
    simulates: 'a test that fails on the original code but passes once instrumentation changes source text',
    test: killing + checkedTest + `it('serialised clamp is large enough', () => {
  expect(clamp.toString().length).toBeGreaterThan(250);
});
`},
  {name: 'real-failure', realism: 'common',
    simulates: 'a genuinely failing test (a real bug or broken expectation)',
    test: killing + checkedTest + `it('broken expectation', () => {
  expect(clamp(9, 0, 3)).toBe(9);
});
`},
  {name: 'stack-line', realism: 'plausible',
    simulates: 'a test asserting the source line of a thrown error',
    test: killing + checkedTest + `it('reports the throwing line', () => {
  let stack = '';
  try { checked(-1); } catch (error) { stack = String(error.stack); }
  expect(stack).toMatch(/math\\.ts:8:/);
});
`},
  {name: 'arity-and-name', realism: 'plausible',
    simulates: 'tests asserting Function.length and Function.name',
    test: killing + checkedTest + `it('keeps the signature', () => {
  expect(clamp.length).toBe(3);
  expect(clamp.name).toBe('clamp');
  expect(checked.length).toBe(1);
});
`},
];

function run(project, skip) {
  const report = join(project, skip ? 'skip.json' : 'default.json');
  const env = {...process.env};
  delete env.OXIMUT_EXPERIMENTAL_SKIP_ORIGINAL_BASELINE;
  if (skip) env.OXIMUT_EXPERIMENTAL_SKIP_ORIGINAL_BASELINE = '1';
  const result = spawnSync(binary, ['--root', project, '--config', join(project, 'vitest.config.ts'),
    '--source', join(project, 'src/math.ts'), '--execution', 'persistent', '--test-selection',
    '--no-cache', '--report', report], {env, encoding: 'utf8', timeout: 600_000});
  const document = JSON.parse(readFileSync(report, 'utf8'));
  const baselineDiagnostics = document.diagnostics.filter((line) =>
    /baseline|experimental/.test(line) && !line.startsWith('execution='));
  const summary = document.summary;
  const detected = result.status === 2 || !summary.complete || document.baseline.outcome !== 'passed';
  return {exit: result.status, detected, baseline: document.baseline.outcome, tests: document.baseline.tests,
    complete: summary.complete, score: summary.score, killed: summary.killed, survived: summary.survived,
    total: summary.total, diagnostics: baselineDiagnostics,
    errors: (document.baseline.errors ?? []).map((error) => String(error).slice(0, 160))};
}

// Inside the repository so the cases resolve vitest from its node_modules.
const root = mkdtempSync(join(import.meta.dirname, '.work-'));
const rows = [];
try {
  for (const entry of cases) {
    const project = join(root, entry.name);
    mkdirSync(join(project, 'src'), {recursive: true});
    mkdirSync(join(project, 'test'), {recursive: true});
    cpSync(join(repo, 'fixtures/pure/vitest.config.ts'), join(project, 'vitest.config.ts'));
    writeFileSync(join(project, 'src/math.ts'), source);
    writeFileSync(join(project, 'test/math.test.ts'), head + entry.test);
    const original = run(project, false);
    const skip = run(project, true);
    const row = {case: entry.name, simulates: entry.simulates, realism: entry.realism, original, skip,
      silentScoreChange: !original.detected && !skip.detected && original.score !== skip.score,
      missedBySkip: original.detected && !skip.detected};
    rows.push(row);
    process.stderr.write(`${entry.name}: default ${original.detected ? 'DETECTED' : 'complete'} ` +
      `(score ${original.score}), skip ${skip.detected ? 'DETECTED' : 'complete'} (score ${skip.score})\n`);
  }
} finally {
  rmSync(root, {recursive: true, force: true});
}
const sensitive = rows.filter((row) => row.case !== 'control');
const totals = {
  cases: sensitive.length,
  detectedWithOriginal: sensitive.filter((row) => row.original.detected).length,
  detectedWithoutOriginal: sensitive.filter((row) => row.skip.detected).length,
  missedWithoutOriginal: sensitive.filter((row) => row.missedBySkip).length,
  controlConsistent: rows[0].original.score === rows[0].skip.score && !rows[0].original.detected && !rows[0].skip.detected,
};
const result = {binary, node: process.version, vitest: JSON.parse(readFileSync(join(repo, 'node_modules/vitest/package.json'))).version,
  oximut: execFileSync(binary, ['--version'], {encoding: 'utf8'}).trim(), totals, rows};
if (output) writeFileSync(output, JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify(totals));
