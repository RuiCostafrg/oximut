import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: {
    include: [
      'test/enabled.test.ts',
      'test/boundary.test.ts',
      'test/arithmetic.test.ts',
      'test/hooks-and-duplicate-names.test.ts',
      'test/shared-state.test.ts',
      'test/uncovered.test.ts',
    ],
    pool: 'forks',
    maxWorkers: 1,
    fileParallelism: false,
    sequence: { concurrent: false },
    testTimeout: 2000,
    hookTimeout: 2000,
  },
});
