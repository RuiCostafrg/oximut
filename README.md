# Oximut

Mutation testing for JavaScript and TypeScript, designed around a Rust + Oxc
instrumentation engine and a thin Vitest adapter.

**Status: M0 feasibility prototype.** The repository contains runnable Vitest
lifecycle experiments, baseline benchmarks, and an Oxc parser smoke check. It does
not yet generate or execute its own mutants, and there is no published package.
Faster execution and lower memory use than Stryker remain hypotheses to measure.
Oximut is a working name.

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
- [Vitest lifecycle decision](docs/adr/0001-vitest-lifecycle.md): measured reuse boundaries.
- [Benchmark harness](benchmarks/README.md): commands and measurement limitations.

## Run the feasibility checks

Use Linux, Node **26.3.1**, npm **11.16.0**, Python **3.13**, and Rust **1.96.0**.
Oxc is pinned to **0.152.0**, compatible with the pinned Rust toolchain. The
JavaScript lockfile pins Vitest/coverage-v8 **4.1.10** and Stryker **10.0.0**.

```sh
npm ci
npm test
npm run experiment
cargo test --workspace --locked
cargo run --locked -p oximut-oxc-smoke -- fixtures/pure/src/logic.ts fixtures/stateful/src/service.ts
```

The experiment writes `.oximut/experiment.json`. It checks discovery, selection,
repeated execution, coverage, import-time environment changes, and cancellation.
The parser smoke check validates dependency compatibility and parsing only; it is
not semantic analysis or a mutation engine.

For baseline timings, choose a new output directory for every invocation:

```sh
python3 benchmarks/run.py --repetitions 5 --warmups 1 --output .oximut/baseline-01
```

This measures ordinary Vitest and Stryker on the same fixtures. It does not compare
an Oximut mutation engine with Stryker. Keep other builds/tests idle while measuring.
The process-tree checks and measurement harness require Linux `/proc`; the adapter
is deliberately restricted to the pinned Vitest version for this milestone.

When running Ubuntu through Android/Termux PRoot, put a Linux Node executable on
`PATH`. An inherited Android Node can fail to load native bindings from Ubuntu
paths. The recorded local measurements describe that environment explicitly.

## Proposed layout

The production components below remain planned; the M0 adapter and fixtures now
exist, along with the isolated `experiments/oxc-smoke` Rust package.

- `crates/oximut-cli`: configuration, scheduling, resource limits, reporting.
- `crates/oximut-instrument`: Oxc parsing, mutation generation, source maps.
- `crates/oximut-core`: mutant identities, protocol types, cache fingerprints.
- `packages/vitest-adapter`: Node worker, Vitest lifecycle, transform plugin, coverage hooks.
- `fixtures`: semantic and isolation regression projects.
- `benchmarks`: pinned projects, runner scripts, raw measurements.

## Initial scope

Node execution with Vitest, JavaScript/TypeScript ESM, and eventually a small
documented set of expression mutations. Expand compatibility from the pinned
combination only when supported by tests.

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
