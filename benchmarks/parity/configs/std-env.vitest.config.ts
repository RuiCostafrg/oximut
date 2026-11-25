import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Parity corpus config for std-env: the upstream test files named below, run under the
// isolation Oximut requires (node environment, one fork, sequential files).
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: {
    include: ['test/agents.test.ts', 'test/index.test.ts'],
    globals: true,
    environment: 'node',
    pool: 'forks',
    maxWorkers: 1,
    fileParallelism: false,
    isolate: true,
  },
});
