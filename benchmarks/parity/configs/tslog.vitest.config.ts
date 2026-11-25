import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Parity corpus config for tslog: the upstream test files named below, run under the
// isolation Oximut requires (node environment, one fork, sequential files).
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: {
    include: ['tests/52_throttle.test.ts', 'tests/45_http_transport.test.ts', 'tests/26_advanced_masking.test.ts', 'tests/71_json_masking_residuals.test.ts', 'tests/77_mask_tojson.test.ts'],
    globals: true,
    environment: 'node',
    pool: 'forks',
    maxWorkers: 1,
    fileParallelism: false,
    isolate: true,
    setupFiles: ['tests/setup.ts'],
    testTimeout: 30000,
  },
});
