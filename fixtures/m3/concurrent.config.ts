import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: {
    include: ['test/concurrent.test.ts', 'test/enabled.test.ts'],
    pool: 'forks',
    maxWorkers: 1,
    fileParallelism: false,
    sequence: { concurrent: false },
    testTimeout: 2000,
    hookTimeout: 2000,
  },
});
