import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: {
    include: ['test/setup-alpha.test.ts', 'test/setup-beta.test.ts'],
    setupFiles: ['./test/setup.ts'],
    pool: 'forks',
    maxWorkers: 1,
    fileParallelism: false,
    sequence: { concurrent: false },
    testTimeout: 2000,
    hookTimeout: 2000,
  },
});
