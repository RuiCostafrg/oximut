# M0 feasibility results

The M0 checks pass. Proceed to M1 with a fresh-controller/process correctness
reference. Controller reuse is experimentally viable on these fixtures; persistent
test-worker reuse and a performance benefit remain unproven. Oximut does not yet
implement mutation generation or provide a speed comparison with Stryker.

## Provenance and reproduction

Baseline implementation: `95513f093b32f842ff63190fccf1969e8a348f5d`, clean tracked
checkout at measurement start. [metadata.json](metadata.json) records source
SHA-256 hashes, exact versions, machine state, and measurement policy.

Local environment: Android 12 ARM64 host, Ubuntu through PRoot, eight logical CPUs.
Node is the Linux ARM64 26.3.1 binary; npm 11.16.0; Python 3.13.13 from Termux;
Rust 1.96.0; Oxc 0.152.0; Vitest/coverage-v8 4.1.10; Stryker/core and runner
10.0.0; TypeScript 7.0.2. Background load was recorded, not controlled.

```sh
npm ci
npm test
npm run experiment
cargo test --workspace --locked
python3 benchmarks/run.py --repetitions 5 --warmups 1 --timeout 180 --output .oximut/m0-reproduction
```

Use a new output directory and the pinned Linux Node on PATH. The recorded baseline
started on 2026-10-23 UTC. Each fixture/tool combination has one warmup and five
measured process-cold runs; OS caches are left untouched. Tool/fixture order reverses
between rounds. Stryker uses per-test coverage, concurrency one, no incremental
results, and no TypeScript checker. Both benchmark configurations use one Vitest
thread worker with file parallelism disabled.

## Baselines

- **pure / stryker**: wall median 96.18 s (range 80.46–97.10 s); CPU median 65.66 s; sampled aggregate peak RSS median 378.6 MiB; peak process count median 4.
- **pure / vitest**: wall median 2.17 s (range 1.78–2.30 s); CPU median 1.65 s; sampled aggregate peak RSS median 97.8 MiB; peak process count median 1.
- **stateful / stryker**: wall median 35.60 s (range 34.77–37.07 s); CPU median 22.68 s; sampled aggregate peak RSS median 374.4 MiB; peak process count median 4.
- **stateful / vitest**: wall median 3.53 s (range 2.40–3.71 s); CPU median 2.54 s; sampled aggregate peak RSS median 104.1 MiB; peak process count median 1.

All 24 invocations succeeded without timeout or descendant cleanup being required.
All five measured repetitions agree on test identities/counts and mutant outcomes:

- Pure fixture: seven tests; Stryker generated 83 mutants, with 76 killed and seven survived.
- Stateful fixture: four tests; Stryker generated 16 mutants, with 14 killed, one survived, and one without coverage.

The harness also verifies positive test execution for killed/survived mutants and
a known body-removal mutant that must be killed, protecting against empty-test
false success.

These are different workloads: ordinary Vitest executes tests, while Stryker also
instruments and evaluates mutants. Their timing ratio is not a mutation-runner
speedup. Tiny fixtures and this PRoot host are not representative of large projects.

## Lifecycle evidence and decision

[lifecycle.json](lifecycle.json) preserves the final experiment output, completed
immediately before the baseline implementation commit. All 12 acceptance gates
passed: baseline success, repeated fresh/reused equivalence, stable identities,
selection equivalence, coverage collection, isolated module reevaluation,
CPU-hang cancellation, absolute deadlines, clean probe execution, environment
updates on reused controllers, a fresh environment reference, and detached-child
cleanup on normal close.

Observed worker PIDs differ across repeated executions and modules are reevaluated.
Reuse therefore means controller reuse, not persistent test workers. Coverage is
whole-run coverage; per-test attribution remains future work. Descendants can
remain alive until controller close, and the prototype depends on Linux/Python
supervision. No memory budget is enforced yet.

Continue with the [lifecycle ADR](../../../docs/adr/0001-vitest-lifecycle.md): build
a correct M1 mutation slice using a fresh-process reference, then evaluate worker
persistence and invalidation independently. Caching, precise coverage selection,
and resource budgets remain later milestones.

## Raw evidence and accounting limits

- [summary.json](summary.json): medians, ranges, and agreement flags.
- [measurements.jsonl](measurements.jsonl): all 24 invocations and measurements.
- [metadata.json](metadata.json): source hashes, configuration, runtime and host details.
- [lifecycle.json](lifecycle.json): adapter experiment and acceptance gates.
- Each `measured-*` and `warmup-*` directory retains `report.json` and `output.log`.
  Absolute paths in reports describe the original checkout; artifacts are retained
  here under their corresponding relative directory names.

RSS is the sampled sum across observed processes at a nominal 20 ms interval;
shared pages may be counted more than once and short peaks may be missed. CPU uses
wait4 accounting for direct/adopted children. Process counts exclude threads and
the measurement harness. Phase metrics use explicit tool output only; unreported
discovery, instrumentation, transform, cache, and reporting phases remain
unavailable. See the [harness documentation](../../README.md) for details.
