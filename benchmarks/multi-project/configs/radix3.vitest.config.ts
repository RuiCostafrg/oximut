import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Multi-project benchmark config for radix3: the upstream test files that run with only Vitest and
// Node (find.test.ts and find-all.test.ts import oxfmt, regexp.pcre.test.ts spawns external PCRE
// tools, wpt.test.ts compares with Node's own URLPattern, which differs on one case), under the isolation Oximut requires (node environment, one fork, sequential files).
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: {
    include: [
      'test/backtracking.test.ts',
      'test/compiler.test.ts',
      'test/group-names.test.ts',
      'test/method-agnostic.test.ts',
      'test/overlap.test.ts',
      'test/regexp-to-route.test.ts',
      'test/regexp.test.ts',
      'test/route-node-keys.test.ts',
      'test/router.test.ts',
      'test/scenarios.test.ts',
      'test/star.test.ts',
      'test/suffix.test.ts',
    ],
    environment: 'node',
    pool: 'forks',
    maxWorkers: 1,
    fileParallelism: false,
    isolate: true,
  },
});
