# Oximut fixture projects

`m1` is the end-to-end mutation acceptance fixture: boolean inversion and a
comparison boundary change are killed, while an uncalled boolean function
survives. `python3 -m unittest discover -s tests -v` checks the Rust CLI and adapter
together, including deterministic generation and unchanged fixture sources.

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

## M2 isolation corpus

`m2` exercises import-time boolean mutations, nested operand side effects,
async and generator expressions, comparison boundaries, and a known survivor.
`tests/test_m2.py` compares it, `m1`, and `stateful` against fresh execution in
two shuffled persistent orders, and verifies source files remain unchanged.

## M3 selection corpus

`m3` exercises independent test files, uncovered functions, duplicate test names,
hooks, and sequential shared state. Separate configurations exercise import-time
execution, concurrent tests, and global setup. `tests/test_m3.py` compares selected
runs against full-suite execution and checks selection counts and fallback reasons.
