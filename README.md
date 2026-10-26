# Oximut

Mutation testing for JavaScript and TypeScript, designed around a Rust + Oxc
instrumentation engine and a thin Vitest adapter.

**Status: M6 benchmark and alpha release evaluation.** The repository generates JS/TS mutants with
Oxc and executes them with bounded worker concurrency, with text and JSON reports. Fresh execution
remains the default correctness reference; opt-in persistent execution shares
transformed code and a Vitest controller while retaining fresh test processes.
Opt-in test selection collects baseline mutant hits and retains covering test files,
with full-suite fallbacks when attribution is uncertain.
Opt-in caching separates instrumentation from outcomes and validates the baseline
on every run. Outcome reuse requires an explicit declaration of complete inputs.
Resource admission accounts for the complete owned process tree, reserves memory
headroom, and reports resource exhaustion separately from mutation kills.
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
- [Roadmap to 1.0.0](docs/release-roadmap.md): remaining release gates and implementation order.
- [M1 runner](docs/m1.md): commands, report semantics, exit codes, and limitations.
- [M2 execution](docs/m2.md): switching, persistent controllers, isolation, and validation.
- [M3 selection](docs/m3.md): baseline attribution, conservative selection, and fallbacks.
- [M4 cache](docs/m4.md): conservative fingerprints, input declarations, and forced execution.
- [M5 budgets](docs/m5.md): worker limits, memory units, admission, and enforcement limits.
- [M6 decision](docs/m6.md): measured scope, release gates, and remaining work.
- [Compatibility](docs/compatibility.md), [report schema](docs/report-schema.md), and [source installation](docs/install.md).
- [Architecture](docs/architecture.md): ownership, execution protocol, and correctness rules.
- [Benchmark plan](docs/benchmarks.md): fair comparison with Stryker and success criteria.
- [Vitest lifecycle decision](docs/adr/0001-vitest-lifecycle.md): measured reuse boundaries.
- [Recorded M0 results](benchmarks/results/2026-10-23-m0-arm64/README.md): five-repeat baselines and lifecycle evidence.
- [Recorded M6 results](benchmarks/results/2026-10-26-m6-arm64/README.md): clean and incremental measurements, regressions, and release decision.
- [Benchmark harness](benchmarks/README.md): commands and measurement limitations.

## Run the feasibility checks

Use Linux, Node **26.3.1**, npm **11.16.0**, Python **3.13**, and Rust **1.96.0**.
Oxc is pinned to **0.152.0**, compatible with the pinned Rust toolchain. The
JavaScript lockfile pins Vitest/coverage-v8 **4.1.12** and Stryker **10.0.0**.
Historical M0/M2 benchmark results used Vitest **4.1.10**; the security patch
upgrade does not update those recorded measurements.

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

CI runs on GitHub-hosted runners with a read-only repository token and checkout
credential persistence disabled. Actions are pinned to full commit SHAs;
Dependabot proposes weekly updates to those pins. Superseded runs are cancelled,
and artifact uploads are restricted to baseline measurement results with seven
day retention. CI audits npm dependencies for moderate or higher advisories;
Dependabot also proposes npm and Cargo dependency updates.

## Layout

The M1 components implement the correctness reference. M2 adds controller reuse
and mutation switching. M3 adds opt-in conservative test selection. M4 adds
opt-in instrumentation and outcome caching. M5 adds central worker scheduling
and best-effort memory admission.

- `crates/cli`: resource admission, isolated workers, configuration, and reporting.
- `crates/instrument`: Oxc parsing, mutation generation, source maps.
- `crates/core`: mutant identities, protocol types, and outcome semantics.
- `packages/vitest-adapter`: Node worker, Vitest lifecycle, and transform plugin.
- `fixtures`: semantic and isolation regression projects.
- `benchmarks`: pinned projects, runner scripts, raw measurements.

## Initial scope

Node execution with Vitest, JavaScript/TypeScript ESM, and a small
documented set of expression mutations. Expand compatibility from the pinned
combination only when supported by tests.

Browser mode, Jest, framework-specific source formats, and TypeScript semantic
checking are deferred. Unsupported constructs
must be reported, not silently treated as covered or successfully tested.
Concurrent workers require tests whose external inputs and outputs are isolated;
the default remains one worker.

## References

- [Oxc parser](https://oxc.rs/docs/guide/usage/parser)
- [Vitest advanced APIs](https://vitest.dev/guide/advanced/)
- [Vitest reporters](https://vitest.dev/guide/advanced/reporters.html)
- [Stryker architecture](https://stryker-mutator.io/docs/stryker-js/guides/create-a-plugin/)
- [Stryker configuration](https://stryker-mutator.io/docs/stryker-js/configuration/)

Licensed under [MIT](LICENSE). `oximut` remains a working name; see the
[M6 decision](docs/m6.md) before treating this prototype as a public alpha.
