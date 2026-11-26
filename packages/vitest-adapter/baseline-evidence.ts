import { writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import type { TestModule } from 'vitest/node';
import { MAX_RESPONSE_BYTES, type Coverage, type Request, type Result } from './protocol.ts';

/**
 * What a baseline run reports besides its outcome: how long each test file took
 * (the CLI derives mutant deadlines from it) and the per-test coverage, which
 * must fit the response limit however large the suite is.
 */

/** The envelope, up to eight escaped 2 KiB errors, helper lists: everything but the evidence. */
const RESERVED_BYTES = 128 * 1024;
/** Timings are a convenience, never worth failing a response over. */
const MAX_TIMINGS_BYTES = 128 * 1024;

export const COVERAGE_TOO_LARGE = 'coverage-payload-too-large';
/** The CLI refuses coverage files above this size, so the adapter does not write them. */
export const MAX_COVERAGE_FILE_BYTES = 512 * 1024 * 1024;

const size = (value: unknown): number => Buffer.byteLength(JSON.stringify(value));

/** How long each module took in its worker: import, setup and tests. */
export function fileTimings(modules: ReadonlyArray<TestModule>): Result['fileTimings'] {
  const timings: NonNullable<Result['fileTimings']> = [];
  for (const module of modules) {
    let diagnostic: ReturnType<TestModule['diagnostic']>;
    try {
      diagnostic = module.diagnostic();
    } catch {
      continue;
    }
    const durationMs = [
      diagnostic.environmentSetupDuration,
      diagnostic.prepareDuration,
      diagnostic.collectDuration,
      diagnostic.setupDuration,
      diagnostic.duration,
    ].reduce((total, part) => total + (Number.isFinite(part) && part > 0 ? part : 0), 0);
    // Whole milliseconds, rounded up: a deadline built on it errs on the long side.
    timings.push({ file: resolve(module.moduleId), durationMs: Math.ceil(durationMs) });
  }
  return timings.sort((a, b) => a.file.localeCompare(b.file));
}

/**
 * Hit lists as inclusive index runs into the sorted test inventory. Tests of one
 * file are contiguous in that order, so a mutant that every test of a large suite
 * reaches costs a few runs instead of one id per test.
 */
export function compactHits(coverage: Coverage): Coverage {
  const index = new Map(coverage.tests.map((test, position) => [test.id, position]));
  return {
    ...coverage,
    hits: coverage.hits.map((hit) => {
      // An unknown id stays explicit: the CLI rejects it as it always did.
      const known = hit.tests.filter((id) => index.has(id));
      const unknown = hit.tests.filter((id) => !index.has(id));
      if (unknown.length) return hit;
      const positions = known.map((id) => index.get(id) as number).sort((a, b) => a - b);
      const runs: [number, number][] = [];
      for (const position of positions) {
        const last = runs[runs.length - 1];
        if (last && position === last[1] + 1) last[1] = position;
        else runs.push([position, position]);
      }
      return { mutantId: hit.mutantId, tests: [], outsideTest: hit.outsideTest, testRanges: runs };
    }),
  };
}

let files = 0;

/**
 * Writes the coverage to a new private file in the run's coverage directory, so a
 * suite's inventory is not bound by the response limit. Returns the file's name, or
 * undefined (nothing written) when the directory or the size does not allow it.
 */
function writeCoverageFile(directory: string, coverage: Coverage): string | undefined {
  try {
    const text = JSON.stringify(coverage);
    if (Buffer.byteLength(text) > MAX_COVERAGE_FILE_BYTES) return undefined;
    const name = `coverage-${process.pid}-${files++}.json`;
    // `wx`: never replace or follow anything already there; owner-only.
    writeFileSync(join(directory, name), text, { flag: 'wx', mode: 0o600 });
    return name;
  } catch {
    return undefined;
  }
}

/**
 * Attaches the baseline's evidence to `result`.
 * Coverage goes to a file in the run's coverage directory when the CLI offered one
 * (no response limit applies to it). Otherwise it must fit the response limit, and
 * coverage that cannot be made to fit is dropped with a reason, so the run goes on
 * with the whole suite for every mutant instead of failing its baseline.
 */
export function attachBaselineEvidence(
  job: Request,
  modules: ReadonlyArray<TestModule>,
  result: Result,
): void {
  if (job.kind !== 'baseline') return;
  const timings = fileTimings(modules);
  if (timings?.length && size(timings) <= MAX_TIMINGS_BYTES) result.fileTimings = timings;
  const { coverage } = result;
  if (!coverage) return;
  const file = job.coverageDir ? writeCoverageFile(job.coverageDir, coverage) : undefined;
  if (file) {
    result.coverage = undefined;
    result.coverageFile = file;
    return;
  }
  const budget =
    MAX_RESPONSE_BYTES - RESERVED_BYTES - (result.fileTimings ? size(result.fileTimings) : 0);
  if (size(coverage) <= budget) return;
  const compact = compactHits(coverage);
  if (size(compact) <= budget) {
    result.coverage = compact;
    return;
  }
  result.coverage = {
    complete: false,
    tests: [],
    hits: [],
    reasons: [...new Set([...coverage.reasons, COVERAGE_TOO_LARGE])].sort(),
  };
}
