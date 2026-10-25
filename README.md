# Oximut

Mutation testing for JavaScript and TypeScript, designed around a Rust + Oxc
instrumentation engine and a thin Vitest adapter.

**Status: M3 conservative test selection.** The repository generates JS/TS mutants with
Oxc and executes them sequentially, with text and JSON reports. Fresh execution
remains the default correctness reference; opt-in persistent execution shares
transformed code and a Vitest controller while retaining fresh test processes.
Opt-in test selection collects baseline mutant hits and retains covering test files,
with full-suite fallbacks when attribution is uncertain.
It also retains the M0 lifecycle experiments and baseline benchmarks.
There is no published package.
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
- [M1 runner](docs/m1.md): commands, report semantics, exit codes, and limitations.
- [M2 execution](docs/m2.md): switching, persistent controllers, isolation, and validation.
- [M3 selection](docs/m3.md): baseline attribution, conservative selection, and fallbacks.
- [Architecture](docs/architecture.md): ownership, execution protocol, and correctness rules.
- [Benchmark plan](docs/benchmarks.md): fair comparison with Stryker and success criteria.
- [Vitest lifecycle decision](docs/adr/0001-vitest-lifecycle.md): measured reuse boundaries.
- [Recorded M0 results](benchmarks/results/2026-10-23-m0-arm64/README.md): five-repeat baselines and lifecycle evidence.
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

## Layout

The M1 components implement the correctness reference. M2 adds controller reuse
and mutation switching. M3 adds opt-in conservative test selection. Outcome
caching and resource budgeting remain planned.

- `crates/cli`: sequential execution, configuration, and reporting.
- `crates/instrument`: Oxc parsing, mutation generation, source maps.
- `crates/core`: mutant identities, protocol types, and outcome semantics.
- `packages/vitest-adapter`: Node worker, Vitest lifecycle, and transform plugin.
- `fixtures`: semantic and isolation regression projects.
- `benchmarks`: pinned projects, runner scripts, raw measurements.

## Initial scope

Node execution with Vitest, JavaScript/TypeScript ESM, and a small
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
