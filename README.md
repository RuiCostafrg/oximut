# Oximut

Mutation testing for JavaScript and TypeScript, designed around a Rust + Oxc
instrumentation engine and a thin Vitest adapter.

**Status: planning.** There is no runnable engine or published package yet.
Faster execution and lower memory use than Stryker are hypotheses to measure,
not established results. Oximut is a working name.

## Goals

- Reuse test workers without changing test outcomes.
- Run the tests that cover each mutant, with conservative fallbacks.
- Cache results only when their inputs remain valid.
- Control total process-tree memory and avoid excessive concurrency.
- Explain every mutant result and every fallback in machine-readable reports.

## Start here

- [Implementation plan](PLAN.md): milestones, dependencies, and acceptance gates.
- [Architecture](docs/architecture.md): ownership, execution protocol, and correctness rules.
- [Benchmark plan](docs/benchmarks.md): fair comparison with Stryker and success criteria.

The first deliverable is a measured vertical slice: instrument one TypeScript
module, run its Vitest tests, and classify its mutants with a reliable reference
execution mode. Persistent execution follows only when equivalent results can
be demonstrated.

## Proposed layout

These components are planned; their source directories do not exist yet.

- `crates/oximut-cli`: configuration, scheduling, resource limits, reporting.
- `crates/oximut-instrument`: Oxc parsing, mutation generation, source maps.
- `crates/oximut-core`: mutant identities, protocol types, cache fingerprints.
- `packages/vitest-adapter`: Node worker, Vitest lifecycle, transform plugin, coverage hooks.
- `fixtures`: semantic and isolation regression projects.
- `benchmarks`: pinned projects, runner scripts, raw measurements.

## Initial scope

Node execution with Vitest, JavaScript/TypeScript ESM, and a small documented set
of expression mutations. Pin one tested Node/Vitest/Oxc combination during the
feasibility milestone. Expand compatibility from evidence.

Browser mode, Jest, framework-specific source formats, simultaneous active
mutants, and TypeScript semantic checking are deferred. Unsupported constructs
must be reported, not silently treated as covered or successfully tested.

## References

- [Oxc parser](https://oxc.rs/docs/guide/usage/parser)
- [Vitest advanced APIs](https://vitest.dev/guide/advanced/)
- [Vitest reporters](https://vitest.dev/guide/advanced/reporters.html)
- [Stryker architecture](https://stryker-mutator.io/docs/stryker-js/guides/create-a-plugin/)
- [Stryker configuration](https://stryker-mutator.io/docs/stryker-js/configuration/)

License and public package naming will be decided before distribution.
