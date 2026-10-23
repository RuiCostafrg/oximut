import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const root = fileURLToPath(new URL('.', import.meta.url));
process.env.OXIMUT_FIXTURE_MODE ??= 'fixture';

export default defineConfig({
  root,
  test: {
    include: ['test/**/*.test.ts'],
    pool: 'forks',
    maxWorkers: 1,
    fileParallelism: false,
    isolate: true,
  },
});
