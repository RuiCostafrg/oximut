import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Multi-project benchmark config for es-toolkit: the colocated spec files of the benchmarked
// modules, run under the isolation Oximut requires (node environment, one fork, sequential
// files). Specs that need a DOM (happy-dom), packages the repository does not install (fast-check) or that import the
// built `es-toolkit/compat` package instead of the source are left out.
const modules = ['array', 'math', 'string', 'object', 'predicate', 'util'];
const compat = ['array', 'math', 'object', 'string', 'util', 'predicate'];

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  test: {
    include: [
      ...modules.map(name => `src/${name}/*.spec.ts`),
      ...compat.map(name => `src/compat/${name}/*.spec.ts`),
    ],
    exclude: [
      'src/array/chunk.spec.ts',
      // happy-dom specs: the benchmark is node-only.
      'src/object/cloneDeep.dom.spec.ts',
      'src/predicate/isBrowser.spec.ts',
      'src/compat/array/findLast.spec.ts',
      'src/compat/array/groupBy.spec.ts',
      'src/compat/array/reduce.spec.ts',
      'src/compat/array/reduceRight.spec.ts',
      'src/compat/array/sampleSize.spec.ts',
      'src/compat/array/uniqWith.spec.ts',
      'src/compat/math/divide.spec.ts',
      'src/compat/object/clone.spec.ts',
      'src/compat/object/cloneWith.spec.ts',
      'src/compat/object/pickBy.spec.ts',
      'src/compat/object/toPairs.spec.ts',
      'src/compat/object/toPairsIn.spec.ts',
      'src/compat/object/values.spec.ts',
      'src/compat/object/valuesIn.spec.ts',
      'src/compat/predicate/isEqual.spec.ts',
      'src/compat/predicate/isMatchWith.spec.ts',
    ],
    environment: 'node',
    pool: 'forks',
    maxWorkers: 1,
    fileParallelism: false,
    isolate: true,
  },
});
