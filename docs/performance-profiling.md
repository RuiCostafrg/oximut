# Profiling multi-file mutation runs

Benchmark the whole command before choosing an optimization. Rust parser speed
alone does not establish faster mutation testing: the CLI also owns resource
sampling, adapters, Vitest controllers, fresh test forks, disabled-transform
validation, test selection, and report/cache work.

The initial native matched campaign is archived in
[the benchmark results](../benchmarks/results/2026-10-27-matched-native-initial/README.md).
Its global gate failed, so its ratios are diagnostic only. The larger workload
also revealed eight full disabled-transform validation runs before mutation
execution. These safety checks remain in place.

`resources.phaseWallMs` records monotonic wall durations for input/scheduler
preparation, the original baseline, all disabled-switch validation, the coverage
baseline, post-baseline validation, mutation planning/execution, teardown, and
final input validation. `resources.disabledValidationRuns` records each checked
file and its duration. These are durations within the final attempt; a discarded
cache attempt retains its resource report in diagnostics. They exclude final
report serialization/publication and are not CPU attribution or an exact
partition of external command time. A skipped phase can be near zero. Existing
whole-process-tree wall/CPU/RSS measurements remain the performance evidence.

For a representative comparison, use pinned real upstream production sources
and tests, retain their license/provenance, record every adaptation, and keep the
same test inventory available to both tools. Declare mutant families before
execution. A report-level intersection of two unequal executed workloads cannot
make their total timings comparable. Preserve failed attempts rather than
rerunning until the results look favorable.

The [article on agents and Rust](https://smallcultfollowing.com/babysteps/blog/2026/10/14/agentic-dev-and-rust/)
proposes a benchmark/profile/change feedback loop and points to
[dial9](https://github.com/dial9-rs/dial9). Dial9 provides Rust/Tokio telemetry and
CPU/memory profiling; it is not a replacement for measuring external Stryker and
Vitest process trees. Oximut currently has a synchronous scheduler and no Tokio
runtime. Start with the phase evidence above and separate Node CPU profiles for
Vitest work; add Rust sampling when those measurements identify Rust as material.
Profiling runs must stay separate from scored timing runs, and profiler overhead
must not be presented as a product regression or improvement.

The first runner optimizations retain every baseline/disabled validation and
fresh test fork. The persistent adapter reuses its bounded request buffer across
serial requests. For mutation-only transitions, Vite invalidates the outgoing
and incoming files (all query/SSR variants and importers); unrelated transforms
remain cached. Probe changes still invalidate the entire graph. These changes
require alternating-file, original-source restoration, dynamic-import, selection,
state-isolation, timeout, and descendant-cleanup regression checks. Their effect
on elapsed time, CPU, and RSS must be measured rather than assumed.

A subsequent supervisor candidate reduces full `/proc` discovery during active
execution from every 10ms to every 50ms. Reaping still polls every 10ms. Shutdown
always discovers a fresh full tree immediately after reaping, including newly
adopted descendants, and retains the existing cleanup cadence and bound. Success
requires `waitpid` to report `ECHILD` as well as an empty fresh tree. Historical
identity tracking is sampled, not an exhaustive process audit. This changes no
resource-budget sampling, test isolation, baseline, or mutation workload. Its
performance effect requires a separate matched measurement.

If process discovery fails, the supervisor performs bounded best-effort cleanup
of previously observed identities, including its directly spawned controller.
Each signal requires an independent current start-time check; missing, reused,
malformed or unreadable identities are skipped. Cleanup shares any shutdown
budget already in progress, reaps without blocking, and then propagates the
original error without publishing a successful cleanup report.

Post-job discovery can observe a test fork that exits during `/proc` enumeration.
Before retaining a controller after such an observation, the runner requires a
second empty excess-descendant scan and independently confirms that every excess
PID from the first scan is absent. An existing or unverifiable PID still requires
controller recycling and verified cleanup. No waiting grace period is added.

## Opt-in native CPU capture

`Native diagnostic CPU profiles` is a separate Ubuntu 24.04 workflow. After this
change is in a PR, add the **`native-profile`** label to that PR (and keep it for
subsequent pushes), or dispatch the workflow on the desired branch. PR runs record
the checked-out merge commit; the artifact source archive and hashes identify
what actually ran. It builds one release candidate into a newly created, private
Cargo target directory before capturing anything. Node 26.3.1, Python 3.13, Rust
1.96.0 and action SHAs match the existing native benchmark workflow. There is no
build-cache restore, candidate comparison, scored warmup, or automatic retry.
The separate scored one-warmup/five-round workflow is unchanged.

The helper runs `real_project.py --defer-ratios` once for ordinary Vitest, Oximut,
and Stryker, with all existing workload and measurement gates. Missing or duplicate
cross-tool evidence is a failure, even if a partial run claims success. It retains
the ordinary 102-test baseline, all 41 source files, five declared mutation source
files and exactly ten killed arithmetic edits per mutation tool. Every baseline,
disabled-transform check, fresh test fork, deadline, resource budget, and cache
setting remains unchanged; profiler overhead may cause these gates to fail. Do
not alter the corpus, 500ms assertions, or gates to make a profile pass.

Artifacts retain the built binary, source archive, source/binary/adapter/helper
hashes, build flags and tool versions, full projects, raw logs, reports,
measurements, `.cpuprofile` files, `.pstats` files, and per-process identity records.
Always download failed/incomplete artifacts too. CI attempts artifact upload even
on failure; hard runner loss/cancellation can prevent finalization or upload.
The summary reports workload qualification separately from observed capture
finalization. No comparative ratios or targets are emitted; raw wall/CPU/RSS
values in the underlying qualification artifacts include profiler overhead and
are **not performance evidence**. Never mix them into the scored campaign.

The default local mode inherits `--cpu-prof --cpu-prof-dir=<unique capture directory>`
through `NODE_OPTIONS`. A diagnostic CommonJS preload records PID, parent PID,
Linux start-time identity, thread ID, argv, execArgv, cwd, observed exit status
and cumulative self CPU counters (`process.cpuUsage()`, microseconds) at preload
and observed exit. Exit counters omit later shutdown work and missing exits;
worker threads share process counters, so never sum thread records as processes.
The native default CPU-profile filenames retain PID/thread identifiers. Match
these to the identity records and project cwd to separate adapter, controller,
test forks, ordinary Vitest and Stryker. Open raw `.cpuprofile` files in a compatible
CPU-profile viewer and inspect startup/loading/transforms/test stacks before
choosing an optimization. Node sampling includes idle/external frames and is not
an exact OS CPU total or a wall-phase-to-CPU mapping. Do not sum profile durations
as CPU consumption. Workers can terminate before a dump or identity record; missing
profiles are explicit incomplete evidence, and an inventory with no missing
observed files still cannot prove every process was captured. Orphan raw profiles
remain available for inspection.

A diagnostic PATH `python3` shim uses an absolute real-interpreter shebang and
profiles only the exact adapter `supervisor.py` invocation in the same process.
It leaves `NODE_CHANNEL_FD` and inherited descriptors untouched, preserves target
argv and signal handlers, and propagates exit status/exceptions. Other Python
invocations immediately `exec` the actual interpreter to avoid recursion.
`cProfile.Profile(timer=time.process_time)` measures **supervisor self CPU**,
including Python discovery/accounting calls and CPU charged during native calls;
it excludes sleeping and child CPU. Inspect with `python3 -m pstats <file>` and
sort by cumulative/time. Profile dump completion is distinct from workload
success. SIGKILL or a startup failure can leave only the start identity; signals
are not intercepted to force a dump or extend a deadline.

This minimal layer does not sample Rust stacks or claim an exact CPU partition.
In particular, Rust's resource sampler may be material throughout the run: short
preparation wall time does not establish negligible Rust CPU. Python self CPU and
Node stacks can guide the next experiment, but cannot account for all remaining
CPU or justify subtracting samples to manufacture exact Rust attribution. Do not
sum child rusage twice. The external benchmark sampler is outside measured wait4
product-tree CPU but perturbs wall time. Any future Rust profiler needs a separate
capability-checked diagnostic experiment; no Tokio/dial9 runtime or mandatory
`perf` dependency is added to the synchronous CLI here.

For a prepared native environment, the helper can also be invoked with
`python3 benchmarks/profile_native.py --binary /absolute/prebuilt/oximut --output
/absolute/new/capture`. Build in a fresh private target directory first, retain
build provenance as the workflow does, leave `NODE_OPTIONS` unset, and use the
pinned dependencies. This is a profiling command, not a timing benchmark.

Minimum usable role evidence requires finalized Node profiles or partial segments
for each of ordinary Vitest,
Oximut and Stryker, and a linked Oximut adapter → Python supervisor → Node
controller → test-fork chain, matched using process identities, argv and project
cwd. This is minimum role evidence, not proof of complete process coverage.
Unmatched Node/Python profiles are listed as orphans and fail capture validation;
all raw files remain available. Node exit-record writes are atomic and guarded so
a profiler write failure cannot replace the workload's original exit status.
Initial Node identity writes are also guarded; missing identities remain capture
errors. Segment metadata records a count and first/last sequence names instead of
listing every checkpoint. The workflow displays only bounded status/counter/error
details; full diagnostics and raw profiles remain in the uploaded artifact.
Initial Python identity writes and both interpreters' process-identity reads are
also guarded. An unavailable/invalid start time remains null and cannot validate
a capture; Node skips startup sampling without that identity. A failed initial
identity write in either interpreter remains incomplete even if later writes would
succeed. The supervisor still runs, and diagnostic stderr failures cannot replace
its exit.


### Short-lived worker checkpoints and the first native capture

The [first native capture](https://github.com/RuiCostafrg/oximut/actions/runs/40655919509)
passed workload qualification but **failed capture validation**. Vitest ends its
forks with SIGTERM, preventing exit-time Node dumps and exit observations. The
retained Stryker worker-thread profiles also lack exit observations. This is
incomplete diagnostic evidence, not a product correctness failure or a valid
performance comparison. Raw evidence is archived under
[`2026-10-28-native-profile-incomplete`](../benchmarks/results/2026-10-28-native-profile-incomplete/README.md).

The native workflow now selects explicit `--segments` mode to collect useful
partial stacks from these workers. This mode uses an inspector session per isolate
**instead of** the built-in exit-time profiler. It starts once during preload and
requests **one** unreferenced checkpoint after 50ms: stop → disconnect → atomic
profile write, with no restart, signal interception, shutdown wait, retained event
loop, or changed workload deadline. Identity metadata declares
`maxSegmentsPerIsolate: 1`; the event record retains actual profile timestamps and
stop/disconnect observations. Failures disable capture and retain stderr/error
evidence where possible while preserving workload exit status.

The delay is a scheduling request, not a duration or file-size guarantee. Busy
synchronous JavaScript can postpone the callback indefinitely; a process killed
before it fires leaves no checkpoint. These stacks are biased toward startup and
omit later work, with no upper bound on lost tail. They cannot establish steady-state
costs or whole-process CPU attribution. Even one checkpoint introduces profiling
and serialization overhead that can perturb scheduling. Worker threads have separate segments but share process
CPU counters. Do not combine checkpoint durations, process counters, or full profiles
into an exact CPU total.

Segment validity and usable process-role evidence are reported separately from
exit observations and full capture validation. Segments never turn an incomplete
lifecycle into a complete capture. With `--segments`, the job is expected to retain
an explicit incomplete/failure status even when workload qualification and minimum
usable-role checks pass. Download its artifacts for analysis; do not retry merely
to obtain a green check or relax the missing-exit gate. No performance ratios are
published. Local invocations can select this mode by adding `--segments`.


The [repeated-checkpoint run](https://github.com/RuiCostafrg/oximut/actions/runs/40658648515)
produced 7,416 partial profiles and all minimum-role evidence, but **failed workload
qualification**: Oximut's original baseline hit its unchanged 30-second deadline.
The retained sampled stacks were dominated by `node:inspector` `post` frames. This
motivated the one-checkpoint experiment, not a product optimization or performance
claim. Its full raw artifact and interpretation are retained in
[`2026-10-28-native-profile-overhead`](../benchmarks/results/2026-10-28-native-profile-overhead/README.md).
The deadline, workload, assertions and incomplete-capture gates remain unchanged.


The [single-startup-checkpoint run](https://github.com/RuiCostafrg/oximut/actions/runs/40685338353)
passed the exact workload gate, including all 102 tests and ten shared killed
arithmetic edits. It retained 64 partial segments, including all 38 Oximut test
forks and minimum-role evidence for all tools, with no orphan profiles or recorded
instrumentation errors. Full capture validation still failed: exit/full-profile
evidence remains incomplete. The [capture capsule and interpretation](../benchmarks/results/2026-10-28-native-startup-qualified/README.md)
preserve the raw diagnostic data and provenance. These startup-biased samples and
instrumented Python times identify experiment candidates; they do not establish
whole-process CPU costs, product speedups or progress against the performance targets.
