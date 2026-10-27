# Implementation plan

## Objective

Build a trustworthy mutation runner for JS/TS using Rust + Oxc and Vitest.
Improve elapsed time and memory consumption through execution reuse, test
selection, caching, and resource-aware scheduling. Measure each improvement
separately; do not assume changing implementation language is sufficient.

## M0 — Baseline and integration feasibility

- [x] Pin a supported Node release, Vitest version, Rust toolchain, Oxc crates,
  Stryker version, and dependency lockfiles. Record actual versions in results.
- [x] Create a pure-function fixture and a fixture with imports, mocks, hooks,
  module state, asynchronous tests, and top-level initialization.
- [x] Measure ordinary Vitest and tuned Stryker: elapsed time, CPU time, peak
  process-tree memory, worker count, mutant count, and phase timings.
- [x] Prototype Vitest discovery, filtered reruns, cleanup, cancellation, and
  coverage collection through its supported APIs.
- [x] Establish which state can safely persist: controller, transform cache,
  test process, and module graph. Record required invalidation boundaries.
- [x] Document the exact tested compatibility range and API risks in an ADR.

Implementation evidence: [Vitest lifecycle ADR](docs/adr/0001-vitest-lifecycle.md)
and [recorded M0 results](benchmarks/results/2026-10-23-m0-arm64/README.md).
Phase timings are retained where emitted by the tools; unavailable phases are explicit.
The safe observed reuse boundary is the controller; test-worker processes and
module evaluations are fresh. Transform-cache benefits and persistent test-worker
reuse remain unproven. M1 retains a fresh-process correctness reference.

**Gate:** repeated clean runs agree; the adapter can select tests and terminate
a hung run. Demonstrate fresh versus reused execution equivalence on the stateful
fixture, or document the isolation fallback. Decide whether to continue, narrow
scope, or contribute the measured improvement to an existing runner.

## M1 — Correct vertical slice

Depends on M0.

- [x] Create the Rust workspace and TypeScript adapter package, lock dependencies,
  and add formatting, linting, unit tests, and integration checks in CI.
- [x] Parse JS/TS with Oxc and implement a small operator set: boolean literal
  inversion, comparison boundary changes, and selected arithmetic replacements.
- [x] Generate deterministic mutant IDs, exact source locations, transformed
  code, and source maps; exclude type-only syntax and unsupported contexts.
- [x] Implement versioned request/response IPC and a baseline test run.
- [x] Run one mutant at a time in a fresh process as the correctness reference.
- [x] Produce text and JSON reports with explicit killed, survived, no-coverage,
  invalid, timeout, infrastructure-error, and skipped outcomes.
- [x] Define exit codes and score denominators. Infrastructure failures and
  skipped mutants must never become successful test evidence.

**Gate:** known killed and surviving mutants are classified correctly; with all
mutants disabled, instrumented code matches original behavior. Parse/transform
errors are distinguished from test failures. The user's source tree is unchanged.

Implementation evidence: [M1 runner and semantics](docs/m1.md), Rust unit tests,
[adapter regression tests](packages/vitest-adapter/test/m1.test.mjs), and
[CLI acceptance tests](tests/test_m1.py). The acceptance corpus yields two known
kills and one survivor; all four stateful service mutants are killed. Failure
tests reject malformed/stale IPC and verify detached-process cleanup. M1 reserves
the no-coverage outcome without assigning it; sound attribution remains M3.

## M2 — Persistent execution and isolation

Depends on M1.

- [x] Add mutation switching so compatible mutants share transformed code.
- [x] Keep the adapter and safe Vitest state alive across requests; preserve
  isolation according to the M0 findings.
- [x] Set the active mutant before module evaluation and reset it after each job.
- [x] Reload or restart for import-time mutations and state that cannot be reset.
- [x] Add per-job deadlines, process-tree termination, worker recycling, and
  bounded retries. Distinguish worker crashes from mutant-induced timeouts.
- [x] Compare randomized mutant execution order against the fresh-process reference.

**Gate:** no unexplained outcome disagreements across the isolation corpus;
repeatable measured benefit over the fresh-process reference. If safe reuse is
limited, expose the limitation instead of disabling isolation silently.

Implementation evidence: [M2 execution and limitations](docs/m2.md), switched
instrumentation unit tests, [adapter isolation tests](packages/vitest-adapter/test/m2.test.mjs),
[randomized equivalence tests](tests/test_m2.py), and
[persistent transport failure tests](tests/test_m2_failures.py). Reuse is opt-in
and limited to the controller/transform state; test forks remain fresh.

**Gate result:** passed on the three-fixture corpus. [Recorded M2 measurements](benchmarks/results/2026-10-24-m2-arm64/README.md)
show no disagreements across 14 mutants, with three measured pairs per fixture
and median fresh/persistent ratios of 1.49×, 2.53×, and 1.39×. The scope remains
the pinned Linux/Node/Vitest combination and the documented fallback policies.

## M3 — Precise test selection

Depends on M2.

- [x] Record mutant hits per stable test identity during an unmutated baseline.
- [x] Attribute before/after hooks and suite initialization conservatively.
- [x] Start with sequential tests; detect concurrent tests and unsupported
  attribution, using broader file/suite execution or rejecting the mode clearly.
- [x] Treat code executed during imports and global setup as requiring broader
  execution. Use fresh evaluation when activating import-time mutants.
- [x] Mark no-coverage only when collection is complete and attribution is sound.
- [x] Validate selected-test results against full-suite execution for the same mutants.

**Gate:** no unexplained missed kills versus the full-suite reference on the
selection corpus. Reports include selected test counts and reasons for fallback.

Implementation evidence: [M3 attribution and limitations](docs/m3.md), probe and
selection unit tests, [adapter regressions](packages/vitest-adapter/test/m3.test.mjs),
[selected/full-suite acceptance](tests/test_m3.py), and
[invalid-attribution tests](tests/test_m3_failures.py).

**Gate result:** passed on the selection corpus in fresh and persistent modes,
including hooks, suite initialization, shared module state, duplicate test names,
imports, concurrent tests, and global setup. Selection retains whole hit files;
unsupported or incomplete attribution falls back to the full suite. Function
source reflection and unobserved transforms cannot establish no-coverage. This
remains opt-in and assumes files do not communicate through external state.

## M4 — Reliable incremental cache

Depends on M3.

- [x] Cache instrumentation separately from mutation outcomes.
- [x] Implement a conservative project-wide execution fingerprint first; include
  sources, tests, config/setup files, snapshots, lockfiles, tool/runtime versions,
  operators, adapter behavior, and declared external inputs.
- [x] Run baseline validation even when outcome cache entries are available.
- [x] Add atomic persistence, schema versioning, interrupted-run recovery, and
  explanations for cache misses and reused results.
- [x] Provide a force/no-cache mode; disable outcome caching for undeclared
  nondeterministic external inputs.
- [x] Introduce dependency-granular invalidation only after its graph is proven
  complete. Fall back to whole-project invalidation when uncertain.

**Gate:** changes to every fingerprint input invalidate the affected cache;
cached and forced runs agree on the cache corpus. Failed or incomplete runs do
not produce reusable successful outcomes.

Implementation evidence: [M4 cache contract](docs/m4.md), Rust fingerprint,
integrity, symlink, and transaction unit tests, and
[cache acceptance tests](tests/test_m4.py). Dependency-granular invalidation
remains deferred; outcome invalidation is project-wide. Outcome caching requires
an explicit complete-input assertion and successful fresh baseline validation.

**Gate result:** passed on the cache corpus. Cached and forced runs agree in
fresh and persistent-selection modes; input edits invalidate outcomes, corrupt
entries recover as misses, and failed/incomplete runs publish no new outcome
bundle. Baseline validation is retained even on warm runs. No incremental
performance claim is made.

## M5 — CPU and memory budget

Depends on M2; integrate with M3–M4 before release.

- [x] Define worker count and memory budget configuration with explicit units.
- [x] Measure aggregate controller/adapter/test-process memory, accounting for
  descendants and container limits where available.
- [x] Own concurrency centrally; bound Vitest's internal parallelism to avoid
  multiplying worker pools.
- [x] Use measured worker cost and reserved headroom to admit jobs; pause new
  work and recycle idle workers under pressure.
- [x] Handle a single job exceeding the budget without retry loops or false kills.
- [x] Clearly distinguish best-effort admission control from OS-enforced hard caps.

**Gate:** stress tests show controlled worker admission, clean shutdown, and
explicit resource-exhaustion outcomes. Budget compliance claims specify platform,
measurement method, sampling interval, and enforcement mechanism.

Implementation evidence: [M5 budget contract](docs/m5.md) and the M1–M5
acceptance suites, including admission, recycling, resource exhaustion, sibling
isolation, and shutdown. M6 retains the current validation logs with its results.

## M6 — Benchmark and alpha release decision

Depends on M3, M4, and M5.

- [x] Execute the initial deterministic-fixture campaign from the
  [benchmark protocol](docs/benchmarks.md) and commit raw results. Record broader
  release experiments as follow-up rather than claiming the full protocol passed.
- [x] Resolve classification mismatches before publishing speed comparisons.
  Shared classifications agree; unequal cross-tool scopes suppress speed ratios.
- [x] Publish a compatibility matrix, known limitations, report schema, and
  reproducible commands. Document clean and incremental performance separately.
- [x] Choose a license, check package-name availability, and test source installation
  before any public release.

**Gate:** meet the proposed performance targets or document evidence for revising
the design. An alpha must have passing correctness and resource-failure tests
regardless of performance.

Implementation evidence: [M6 decision](docs/m6.md), [recorded measurements and
validation](benchmarks/results/2026-10-26-m6-arm64/README.md), benchmark gate tests,
and the source-installation smoke. Correctness/resource tests and all seven
changed-input probes pass. Persistent execution improves elapsed time on these
fixtures while retaining slightly more memory; unchanged caching regresses time
on the small M1 fixture. The rejected initial cache-harness campaign is retained.

**Gate result:** hold public alpha performance claims. The measured cache
regression and controller memory cost justify profiling/revising the design;
the Stryker workloads do not yet match exactly. Real projects, generated scaling,
and repeated native-host CPU/memory-budget experiments remain release follow-up,
not completed evidence. No public package has been released.

## Next implementation task

The [1.0.0 roadmap](docs/release-roadmap.md) defines the remaining contract,
validation, distribution, and maintenance gates. The cache follow-up implements
accelerated full hashing, safe all-hit scan consolidation, and automatic
cost-based outcome-cache bypass without weakening baseline/input validation.
See the [cache performance decision](docs/cache-performance.md) for repeatable
measurements, calibration costs, and the retained explicit reuse mode.

Continue R1's CLI/unsupported-case contract audit, then validate the exact release
candidate and distribution (R3–R4) and define support/update policy (R5).
CI validates normal M6 smoke reports against the checked-in JSON Schema;
report and list versioning are documented separately. Equivalent Stryker scope,
real-project/scaling workloads, and native Linux budget measurements remain
necessary before making broader comparative performance claims.

## Decisions still open

- Compatibility expansion beyond the exact versions pinned in M0.
- Broader test-worker reuse beyond the controller boundary proven in M0/M2.
- Hard memory enforcement beyond M5 Linux sampled admission/cancellation.
- Final public package reservation, binary targets/provenance, and a smaller npm
  runtime bundle. M6 chooses MIT and tested Linux source installation; no package
  is published.
- Performance scope: benchmark pure modules and application-like tests separately.
