# Implementation plan

## Objective

Build a trustworthy mutation runner for JS/TS using Rust + Oxc and Vitest.
Improve elapsed time and memory consumption through execution reuse, test
selection, caching, and resource-aware scheduling. Measure each improvement
separately; do not assume changing implementation language is sufficient.

## M0 — Baseline and integration feasibility

- [ ] Pin a supported Node release, Vitest version, Rust toolchain, Oxc crates,
  Stryker version, and dependency lockfiles. Record actual versions in results.
- [ ] Create a pure-function fixture and a fixture with imports, mocks, hooks,
  module state, asynchronous tests, and top-level initialization.
- [ ] Measure ordinary Vitest and tuned Stryker: elapsed time, CPU time, peak
  process-tree memory, worker count, mutant count, and phase timings.
- [ ] Prototype Vitest discovery, filtered reruns, cleanup, cancellation, and
  coverage collection through its supported APIs.
- [ ] Establish which state can safely persist: controller, transform cache,
  test process, and module graph. Record required invalidation boundaries.
- [ ] Document the exact tested compatibility range and API risks in an ADR.

**Gate:** repeated clean runs agree; the adapter can select tests and terminate
a hung run. Demonstrate fresh versus reused execution equivalence on the stateful
fixture, or document the isolation fallback. Decide whether to continue, narrow
scope, or contribute the measured improvement to an existing runner.

## M1 — Correct vertical slice

Depends on M0.

- [ ] Create the Rust workspace and TypeScript adapter package, lock dependencies,
  and add formatting, linting, unit tests, and integration checks in CI.
- [ ] Parse JS/TS with Oxc and implement a small operator set: boolean literal
  inversion, comparison boundary changes, and selected arithmetic replacements.
- [ ] Generate deterministic mutant IDs, exact source locations, transformed
  code, and source maps; exclude type-only syntax and unsupported contexts.
- [ ] Implement versioned request/response IPC and a baseline test run.
- [ ] Run one mutant at a time in a fresh process as the correctness reference.
- [ ] Produce text and JSON reports with explicit killed, survived, no-coverage,
  invalid, timeout, infrastructure-error, and skipped outcomes.
- [ ] Define exit codes and score denominators. Infrastructure failures and
  skipped mutants must never become successful test evidence.

**Gate:** known killed and surviving mutants are classified correctly; with all
mutants disabled, instrumented code matches original behavior. Parse/transform
errors are distinguished from test failures. The user's source tree is unchanged.

## M2 — Persistent execution and isolation

Depends on M1.

- [ ] Add mutation switching so compatible mutants share transformed code.
- [ ] Keep the adapter and safe Vitest state alive across requests; preserve
  isolation according to the M0 findings.
- [ ] Set the active mutant before module evaluation and reset it after each job.
- [ ] Reload or restart for import-time mutations and state that cannot be reset.
- [ ] Add per-job deadlines, process-tree termination, worker recycling, and
  bounded retries. Distinguish worker crashes from mutant-induced timeouts.
- [ ] Compare randomized mutant execution order against the fresh-process reference.

**Gate:** no unexplained outcome disagreements across the isolation corpus;
repeatable measured benefit over the fresh-process reference. If safe reuse is
limited, expose the limitation instead of disabling isolation silently.

## M3 — Precise test selection

Depends on M2.

- [ ] Record mutant hits per stable test identity during an unmutated baseline.
- [ ] Attribute before/after hooks and suite initialization conservatively.
- [ ] Start with sequential tests; detect concurrent tests and unsupported
  attribution, using broader file/suite execution or rejecting the mode clearly.
- [ ] Treat code executed during imports and global setup as requiring broader
  execution. Use fresh evaluation when activating import-time mutants.
- [ ] Mark no-coverage only when collection is complete and attribution is sound.
- [ ] Validate selected-test results against full-suite execution for the same mutants.

**Gate:** no unexplained missed kills versus the full-suite reference on the
selection corpus. Reports include selected test counts and reasons for fallback.

## M4 — Reliable incremental cache

Depends on M3.

- [ ] Cache instrumentation separately from mutation outcomes.
- [ ] Implement a conservative project-wide execution fingerprint first; include
  sources, tests, config/setup files, snapshots, lockfiles, tool/runtime versions,
  operators, adapter behavior, and declared external inputs.
- [ ] Run baseline validation even when outcome cache entries are available.
- [ ] Add atomic persistence, schema versioning, interrupted-run recovery, and
  explanations for cache misses and reused results.
- [ ] Provide a force/no-cache mode; disable outcome caching for undeclared
  nondeterministic external inputs.
- [ ] Introduce dependency-granular invalidation only after its graph is proven
  complete. Fall back to whole-project invalidation when uncertain.

**Gate:** changes to every fingerprint input invalidate the affected cache;
cached and forced runs agree on the cache corpus. Failed or incomplete runs do
not produce reusable successful outcomes.

## M5 — CPU and memory budget

Depends on M2; integrate with M3–M4 before release.

- [ ] Define worker count and memory budget configuration with explicit units.
- [ ] Measure aggregate controller/adapter/test-process memory, accounting for
  descendants and container limits where available.
- [ ] Own concurrency centrally; bound Vitest's internal parallelism to avoid
  multiplying worker pools.
- [ ] Use measured worker cost and reserved headroom to admit jobs; pause new
  work and recycle idle workers under pressure.
- [ ] Handle a single job exceeding the budget without retry loops or false kills.
- [ ] Clearly distinguish best-effort admission control from OS-enforced hard caps.

**Gate:** stress tests show controlled worker admission, clean shutdown, and
explicit resource-exhaustion outcomes. Budget compliance claims specify platform,
measurement method, sampling interval, and enforcement mechanism.

## M6 — Benchmark and alpha release decision

Depends on M3, M4, and M5.

- [ ] Execute the [benchmark protocol](docs/benchmarks.md) and commit raw results.
- [ ] Resolve classification mismatches before publishing speed comparisons.
- [ ] Publish a compatibility matrix, known limitations, report schema, and
  reproducible commands. Document clean and incremental performance separately.
- [ ] Choose a license, confirm package-name availability, and test installation
  before any public release.

**Gate:** meet the proposed performance targets or document evidence for revising
the design. An alpha must have passing correctness and resource-failure tests
regardless of performance.

## First implementation task

Complete M0 with two fixture projects and a minimal adapter experiment. Deliver
raw baseline measurements and an ADR describing the reusable Vitest lifecycle.
Do not build broad mutation support before this experiment answers the main risk.

## Decisions still open

- Exact supported versions and minimum Rust version: choose and lock in M0.
- Safe reuse boundary in Vitest: prove in M0/M2.
- Linux-first hard memory enforcement versus portable soft budgets: decide in M5.
- Public name, license, distribution targets, and npm packaging: decide in M6.
- Performance scope: benchmark pure modules and application-like tests separately.
