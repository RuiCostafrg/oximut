import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Multi-project benchmark config for valibot's `library/` package: every upstream test file
// under src, in the node environment (upstream's jsdom is not needed by the code under test),
// under the isolation Oximut requires (one fork, sequential files, isolated modules).
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: {
    include: ['src/**/*.test.ts'],
    // Needs jsdom's `document` (a cross-realm Date); its source is outside the mutation scope too.
    exclude: ['src/utils/_isValueMatch/_isValueMatch.test.ts'],
    environment: 'node',
    pool: 'forks',
    maxWorkers: 1,
    fileParallelism: false,
    isolate: true,
  },
});
