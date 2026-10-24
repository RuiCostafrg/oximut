import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

/** Stryker supplies repository-relative test filters, so keep the same root. */
export function fixtureVitestConfig(fixture) {
  return defineConfig({
    root: fileURLToPath(new URL('../../', import.meta.url)),
    test: {
      include: [`fixtures/${fixture}/test/**/*.test.ts`],
      pool: 'threads',
      maxWorkers: 1,
      fileParallelism: false,
      isolate: true,
    },
  });
}
