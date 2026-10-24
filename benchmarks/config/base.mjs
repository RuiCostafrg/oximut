/** Clean, process-cold baseline; no outcome cache or semantic TS checker. */
export function fixtureConfig(fixture) {
  if (!process.env.OXIMUT_BENCH_REPORT) {
    throw new Error('Run this configuration through benchmarks/run.py (missing OXIMUT_BENCH_REPORT)');
  }
  return {
    mutate: [`fixtures/${fixture}/src/**/*.ts`],
    ignorePatterns: ['/target/**', '/benchmarks/results/**', '/.oximut/**', '/.stryker-tmp*/**'],
    testRunner: 'vitest',
    vitest: { configFile: `benchmarks/config/vitest-${fixture}.mjs` },
    coverageAnalysis: 'perTest',
    concurrency: 1,
    checkers: [],
    incremental: false,
    reporters: ['json', 'progress'],
    jsonReporter: { fileName: process.env.OXIMUT_BENCH_REPORT },
    tempDirName: `.stryker-tmp-${fixture}`,
    cleanTempDir: true,
  };
}
