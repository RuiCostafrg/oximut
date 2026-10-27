Run from the repository root after `npm ci` using the pinned Node release:

```sh
python3 -m unittest discover -s benchmarks/tests -v
python3 benchmarks/run.py --repetitions 5 --warmups 1 --timeout 180 \
  --output benchmarks/results/YYYY-MM-DD-m0
```

The output directory must not exist. Each tool invocation gets a new report path;
the harness rejects missing, partial, unsuccessful, empty, or unfinished reports.
`--fixtures pure stateful` and `--tools vitest stryker` select workloads. Each tool
and fixture receives one discarded warmup by default, then five measured runs.
Fixture order and tool order reverse on alternate rounds, including warmups.
Every invocation starts fresh processes. Filesystem caches are left untouched.
Dependency installation is outside the measurement. Run the harness alone, with
other repository tests and builds stopped.

Both tools use the benchmark-owned `benchmarks/config/vitest-*.mjs` wrappers,
one thread worker, and no file parallelism. These wrappers keep Vitest rooted at
the repository with fixture-prefixed includes, matching the repository-relative
test filters supplied by Stryker. A nested fixture root can silently rerun zero
tests in this integration. The wrappers select `pool: 'threads'` because Stryker's
Vitest runner enforces that pool; the separate lifecycle adapter experiment uses
the fixture configurations with `pool: 'forks'` and reports that distinction.
Stryker uses its dedicated Vitest runner, per-test coverage,
concurrency one, no incremental outcomes, and no TypeScript checker. Source paths
and config paths remain relative to the repository root, including in Stryker's
sandbox. Reports use absolute paths supplied by the harness, outside the sandbox.
The Stryker baseline may reuse workers *within* one invocation as its tuned default;
this is distinct from Oximut's proposed fresh-process correctness reference.

`metadata.json` records versions, revision, dirty status, source/config/lock hashes,
machine details, affinity, cgroup limits where readable, and initial background
load. `measurements.jsonl` retains every warmup and measured invocation, commands,
exit/timeout status, CPU, memory, observed process counts, artifact validation,
mutation counts and outcome identity, and explicit phase timing output. Every run
retains `output.log` (first 1 MiB by default) and the tool's original `report.json`.
`summary.json` gives medians and min/max spread for successful measured runs only,
plus repeated mutation-count/outcome agreement. A failure anywhere exits nonzero;
disagreement in per-mutant outcomes or ordinary test identities/counts across
measured repetitions also exits nonzero. The harness fixes the fixture's declared
`OXIMUT_FIXTURE_MODE=fixture` input and records this choice in metadata.
Both reports must identify exactly the fixture's checked-in test file set and
test count (seven pure tests or four stateful tests). Stryker's discovered test
identities/counts must also agree across measured repetitions.
Inspect raw rows before using a partial summary. The M0 harness does not execute
Oximut, and these runs establish no mutation-engine speed ratio.

Stryker `Killed`/`Survived` outcomes must include a positive completed-test count.
For each fixture, a known function-body-removal mutant must be killed (the first
`clamp` body in `pure/src/logic.ts`, and `priceAfterAdjustment` in
`stateful/src/service.ts`). This semantic check catches reports that look complete
but rerun zero tests or fail to activate mutants. If those fixture declarations
move, update the sentinel locations in the validator along with the fixture.

CPU is `wait4` user + system rusage for the command and adopted descendants. Linux
subreaper mode allows the harness to reap orphan descendants. A child's rusage
includes descendants it has already reaped; those descendants are not separately
summed. This avoids counting sampled cumulative CPU multiple times. Descendants
that escape observation or are not reaped can undercount usage. Metadata gathering
and the Python harness itself are excluded from workload CPU and memory.

Memory is the maximum sampled sum of `/proc/PID/stat` resident page counts across
the observed process tree, sampled every 20 ms by default. Shared pages count in
each process's RSS, and short-lived processes or peaks between samples can be
missed. On the recorded host, stat RSS also lagged a live allocation by several
seconds while a smaps rollup already showed the resident pages. Stat-only peaks
therefore do not establish precise memory savings. This is an observed aggregate RSS peak, not a PSS/unique-memory figure or
an enforced memory cap. Process count includes the tool controller and workers,
excludes zombie processes, and is not a count of worker threads. Cgroup/affinity
values are recorded, not changed; missing or virtualized counters remain a host
limitation. The harness's process scans add measurement overhead.

Each command has a wall deadline (`--timeout`), including startup and reporting.
On timeout, the harness signals the session's process group and tracked descendants,
then escalates to SIGKILL. It also treats a successful parent that leaves children
behind as a failure and cleans those children up. The kernel can delay termination
of uninterruptible tasks; the harness bounds cleanup and records any failure.
Output capture is bounded while continuing to drain pipes. Tool phase boundaries
are retained only where explicitly emitted; unavailable phase timings, worker
restart counts, and queue depths are not invented.

## M2 fresh versus persistent execution

Build the debug CLI, then run the matched end-to-end comparison:

```sh
cargo build -p oximut-cli --locked
python3 benchmarks/m2.py --repetitions 3 --warmups 1 --output .oximut/m2-benchmark
```

This separate harness alternates mode order, varies mutant order reproducibly,
and rejects incomplete or disagreeing outcomes. It saves all reports and timing
samples, plus environment and implementation hashes. It measures elapsed time
only; the M0 CPU/RSS methodology above does not apply to these samples. See
[M2 semantics](../docs/m2.md) for isolation and scope.

## M6 correctness-gated measurements

Build outside the measurement window and run the harness alone:

```sh
cargo build --release --locked -p oximut-cli
python3 benchmarks/m6.py --binary target/release/oximut \
  --fixtures pure stateful --scenarios clean --repetitions 5 --warmups 1 \
  --output .oximut/m6-clean
python3 benchmarks/m6.py --binary target/release/oximut \
  --fixtures m1 --tools oximut stryker --scenarios incremental --changes source test \
  --repetitions 5 --warmups 1 --output .oximut/m6-incremental
```

Clean scenarios measure ordinary Vitest, fresh Oximut, persistent Oximut,
persistent Oximut with selection, and tuned Stryker. Incremental scenarios
measure a cache prime separately, then unchanged or edited execution paired with
a forced run. Oximut cache pairs keep the live report destination and inherited
benchmark environment stable because both participate in cache identity; each
fresh report is then archived separately. `--changes source test helper snapshot config lockfile environment`
selects input edits; the default selects source and test. These edits preserve
test behavior. The imported helper changes an unused export; the snapshot is an
inert fingerprint probe. They establish invalidation behavior, not sensitivity
to every semantic change. The M4 regression suite supplies additional input cases.
Oximut changed-input gates require a persisted eligible prime and zero reused
outcomes. Stryker pairs record its native reuse and compare outcomes; agreement
after behavior-preserving edits does not prove Stryker invalidates those inputs.

Oximut measurements default to `--cache-policy always`, so unchanged runs must
actually reuse every outcome. Select `--cache-policy auto` for adaptive-policy
measurements. A cost-based bypass must have a validated prime, zero cache
provenance, and no fingerprint profiles; it is compared with forced execution
as fresh execution, and is not credited as cache invalidation or actual reuse.
Auto runs archive their advisory observations as `policy-history.json` outside
the measurement window. Use `--cache-policy legacy` to omit the CLI policy flag
when comparing a binary that predates this option.

Each invocation starts fresh processes and uses isolated fixture copies with
shared installed dependencies. Setup is excluded and recorded; OS filesystem
caches are left untouched. Scenario/fixture order and forced/incremental pair
order alternate. The default is five measured repetitions after one warmup.
`--workers` sets Oximut worker concurrency and Stryker concurrency; ordinary
Vitest stays fixed at `--maxWorkers=1`. `--memory-budget-mib` sets Oximut's soft
admission budget only. Separate runs can explore budgets without implying that
Stryker implements an equivalent cap.

The M0 process-tree CPU/RSS sampler described above supplies the measurements.
Its approximate stat RSS is distinct from current Oximut admission accounting,
which prefers accurate smaps rollups. Do not compare those two internal memory
methods as if an accounting change were a change in actual memory use.
`metadata.json`, `measurements.jsonl`, `measurements.csv`, `summary.json`, and
`comparisons.json` retain commands, configuration hashes, environment, medians,
min/max spread, warmups, failures, overlap/exclusions, and correctness gates.
Raw reports and bounded logs are retained per invocation. Fixture copies are
archived as `project-inputs.json` and removed; `--keep-projects` retains them for
debugging. Reproduction uses the harness command, not stale absolute paths in a
recorded raw invocation.

Mutation identity is the exact edited file content plus relative filename,
including UTF-16 position conversion. Formatting/parentheses differences remain
exclusions. Shared outcomes must agree before comparisons pass. A sound selection
refinement from survived to no-coverage is recorded explicitly. Cross-tool ratios
are suppressed unless the **entire** mutation sets agree; partial overlap does
not justify a full-run speed claim. Any failed report, incomplete outcome,
classification disagreement, or unstable repeated outcome fails the harness.
Ordinary Vitest and Stryker inventories are checked by file/count and compared
across repetitions. Oximut full-run reports expose baseline counts without test
identities; those scenarios validate expected fixture counts, not identity-level
inventory stability. This is a limit of the current report evidence.
Single-repeat smoke/input-change runs are correctness evidence only.

The harness covers deterministic fixtures. Real projects, generated scaling,
resource-failure stress, and public release readiness require separate evidence;
see [the M6 decision](../docs/m6.md).
