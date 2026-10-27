# Benchmark protocol

Historical M0 and M2 measurements are checked in under `benchmarks/results/`.
M6 adds an end-to-end comparison and an explicit alpha decision in
[m6.md](m6.md). Targets below remain engineering goals until equivalent-scope
measurements establish them; a partial mutation overlap cannot establish a speedup.

## Fair comparison

Compare with a pinned Stryker + Vitest runner using per-test coverage, appropriate
concurrency, worker reuse, and incremental mode for incremental scenarios. Record
all configuration and lockfiles. Include Stryker's measured performance so a fast
parser cannot conceal slower overall test execution.

Use equivalent mutation locations and operators. Publish the overlap and exclusions
when tools cannot generate identical mutants. Compare classifications on the shared
set before interpreting speed. Report total generated, tested, skipped, invalid,
cached, killed, surviving, timed-out, and uncovered mutants. A smaller workload is
not an equivalent speedup.

Keep optional TypeScript semantic checking off in both tools for the first matched
comparison. Document any later comparison with checking enabled separately.

## Workloads

1. Pure functions with short tests: expose instrumentation and scheduling overhead.
2. Application-like module graphs with setup, mocks, and asynchronous tests: expose
   transform, reload, and isolation costs.
3. Import-time initialization and static mutants: exercise conservative fallbacks.
4. Large generated corpus: measure instrumentation scaling and report/cache memory.
5. Memory-heavy tests and leaking workers: exercise admission and recycling.
6. Hung tests, crashes, noisy output, and cancellation: verify failure behavior.

Use deterministic checked-in fixtures first. Add real projects at pinned commits
with reproducible installation steps; record permission/license constraints for
redistributing any benchmark inputs.

## Scenarios

- Ordinary uninstrumented test run.
- Clean full mutation run, no outcome cache, fresh processes.
- Same full run with persistent execution.
- Same full run with per-test selection.
- Unchanged incremental run, including baseline cost.
- Incremental runs after changes to source, tests, imported helpers, snapshots,
  config, lockfile, and declared environment input.
- Forced rerun paired with each incremental result for cache validation.
- Fixed CPU allocation under multiple memory budgets.

Distinguish process-cold runs from OS filesystem cache state. Record rather than
silently manipulate host caches. Keep installation/download time outside execution
timings, but report setup costs separately.

## Measurements

- End-to-end wall time and total process-tree CPU time.
- Phase timings: discovery, parsing/instrumentation, transform/build, baseline,
  test execution, cache lookup/write, and reporting.
- Peak aggregate memory with platform/accounting method and sampling interval.
- Actual worker/process counts, restarts, queue depth, and resource-limit events.
- Executed tests per mutant, cache hit/miss reasons, and fallback counts.
- Outcome disagreements and diagnostic logs with bounded capture.

Pin hardware/container CPU and memory allocations. Record OS, architecture, tool
versions, git revisions, commands, seeds, and background load. Use at least five
measured repetitions per ordinary scenario after a documented warmup, alternating
tool order. Report medians and spread, and retain individual measurements.

## Proposed acceptance criteria

Correctness gates are mandatory: no unexplained disagreements between reference
and optimized modes on the regression corpus; no false kills from infrastructure
or resource failures; cache invalidation passes all declared-input cases.

The current requested targets are at least **20% lower full-run elapsed time**
and **50% lower peak aggregate memory** versus tuned Stryker, at equal CPU
allocation and equivalent mutation scope. Expressed as Oximut/Stryker ratios,
the thresholds are wall time ≤0.80 and peak aggregate RSS ≤0.50. Measure and
report total CPU time separately; no numeric CPU-reduction threshold is specified.
These are unachieved targets, not promises or a declaration of release readiness.

Predeclare the primary execution mode and required workloads/scales before
measurement; do not select a favorable mode or discard a failing scale afterward.
A controlled synthetic result supports only that corpus and host. Broader claims
require representative application evidence. Report regressions on every workload,
and revisit the design if gains only occur on synthetic inputs.

Earlier alpha planning proposed 2x lower wall time and 30% lower memory. Those are
historical aspirations, superseded by the current requested targets above;
archived measurements and their original acceptance decisions remain unchanged.

For memory control, demonstrate bounded admission, worker recycling, and explicit
failure when one job cannot fit. Only claim a strict cap where an OS mechanism
enforces it; otherwise publish observed overshoot and sampling limitations.

## Results format

Store raw JSON/CSV plus a short analysis under a dated `benchmarks/results/`
directory once the harness exists. Each row identifies tool/revision, workload,
scenario, repetition, configuration hash, timing, CPU, peak memory, mutant counts,
test counts, and outcome comparison. Preserve commands and machine metadata beside
the data. Never publish a speed ratio without its denominator and workload scope.
