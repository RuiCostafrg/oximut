# Oximut fixture projects

These small ESM TypeScript projects exercise the behaviors needed for the M0
adapter experiment. Run them from the repository root:

```sh
npx vitest run --config fixtures/pure/vitest.config.ts
npx vitest run --config fixtures/stateful/vitest.config.ts
```

`pure` covers comparison boundaries, arithmetic replacements, ternaries,
boolean operators, and short-circuiting through small deterministic functions.
Its tests include values immediately around branch boundaries, so the fixture
should yield a useful initial mutation corpus without relying on randomness.

`stateful` combines an imported dependency mocked with Vitest, sequential hooks,
async calls, a module-level singleton counter, and a value initialized from an
environment variable during import. Its first test requires a zeroed singleton;
the later tests calculate their expected values from their own starting state.
Running the suite again while reusing the same loaded module graph exposes stale
module state. A separate test covers the dependency's real implementation.
The config uses one forked worker, sequential files, and Vitest isolation so a
fresh ordinary run passes.
