/** Copied into isolated M6 fixture roots. Run via benchmarks/m6.py. */
if (!process.env.OXIMUT_BENCH_REPORT) {
  throw new Error('M6 requires OXIMUT_BENCH_REPORT');
}
export default {
  mutate: ['src/**/*.ts', '!src/benchmark-helper.ts'],
  files: ['src/**/*.ts', 'test/**/*.ts', 'test/**/*.snap', 'vitest.config.ts', 'package*.json'],
  testRunner: 'vitest',
  vitest: { configFile: 'vitest.config.ts' },
  coverageAnalysis: 'perTest',
  concurrency: Number(process.env.OXIMUT_BENCH_WORKERS ?? 1),
  checkers: [],
  // Limit the comparison to supported operator families. EqualityOperator still
  // generates extra replacements; m6.py publishes exact overlap and exclusions.
  mutator: {
    excludedMutations: [
      'AssignmentOperator', 'CallExpression', 'ArrayDeclaration', 'ArrowFunction', 'BlockStatement', 'ConditionalExpression',
      'LogicalOperator', 'MethodExpression', 'ObjectLiteral', 'OptionalChaining',
      'Regex', 'StringLiteral', 'UnaryOperator', 'UpdateOperator',
    ],
  },
  incremental: process.env.OXIMUT_BENCH_INCREMENTAL === 'true',
  incrementalFile: process.env.OXIMUT_BENCH_INCREMENTAL_FILE,
  reporters: ['json', 'progress'],
  jsonReporter: { fileName: process.env.OXIMUT_BENCH_REPORT },
  tempDirName: '.stryker-tmp-m6',
  cleanTempDir: true,
};
