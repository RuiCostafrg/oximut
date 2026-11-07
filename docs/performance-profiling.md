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
preparation, the original baseline, the instrumented baseline (inactive switch
validation for every switched file, plus coverage collection when
`--test-selection` is enabled), post-baseline
validation, mutation planning/execution, teardown, and final input validation.
`resources.disabledValidationRuns` records each shared validation run with its
validated files (`files`), duration, and whether it also collected coverage. There
is normally one record; groups split only to keep requests within the adapter's
limit. Earlier binaries recorded one `file` per
separate disabled run and separate `disabledSwitchValidation`/`coverageBaseline` phases. These are durations within the final attempt; a discarded
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

Periodic accounting now discovers only the owned tree. The CLI and each slot
are subreapers, so every owned process, including adopted orphans, is reachable
through the per-thread `/proc/<pid>/task/<tid>/children` links. Periodic resource
samples and the slots' 50ms ownership snapshots follow those links instead of
reading every host process. A walk fails when it observes the tree changing:
a visited or listed process exited (including zombies, whose children have
already moved to a subreaper), one of its threads vanished mid-read (its
children moved to a sibling thread), or a listed PID's parent is outside the tree
(PID reuse or reparenting). That sample then uses broad discovery, as do kernels
without the links. Forced samples (job completion, admission pressure and final
accounting) always use broad discovery. The kernel's `children` lists are only
exact for frozen processes, so a periodic sample can still miss a process that
forks or exits concurrently. That miss lasts at most one interval, the same
order as the sampling interval itself. `resources.treeDiscoveries` and
`resources.broadDiscoveries` count each kind. The CLI no longer enumerates
`/proc` on every 10ms dispatch poll: slot ownership reuses the sampler's
discovery. Waiting for a busy slot is ordinary waiting and forces no samples.
Only memory-pressure admission waits take a broad sample, at most once per
sampling interval. Before this change, every 10ms poll while all slots were busy
(the whole mutation phase with one worker) forced a broad host scan. Every abort, termination and shutdown verification still performs a
fresh broad discovery before signalling or concluding cleanup, so the
fail-closed cleanup gates are unchanged. Identity checks for tree members remain
fresh and strict for every aggregate, slot and CLI-floor observation. Each
verified PID's RSS is read once per sample (one `smaps_rollup` walk) and reused
by the overlapping trees of that sample only; errors are never cached and every
new sample reads afresh.

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

### Qualified job observations with partial CPU sampling (current default)

The performance workstream's [evidence and ranked experiments](edgy-performance.md)
start with a narrower `--controller-jobs` capture. It runs the unchanged exact
three-tool qualification, but preloads instrumentation only in `m2-worker.ts`.
It records bounded fork IPC boundaries for every job and a timeline from request
observation to the local `process.send` return. This boundary is recorded only
after a normal return; it proves neither recipient delivery nor callback
completion. The boolean return is retained unchanged, including `false` for
backpressure, and exceptions/callback semantics are preserved. One inspector CPU
profile samples a shorter interval
within the first mutant request per controller, starting during profiler
activation and stopping after the send returns. Some request-handling prefix is not
sampled; inspector startup/stop overhead may also appear in the profile. No product
adapter code changes. Incoming/outgoing worker control messages separate fork
creation, bootstrap, collection, test completion, shutdown and exit. Only the
first occurrence of each known RPC method is timestamped per fork; exit records
retain its total count. No user request/test/source payloads are logged.

Controller request/reply records also retain `process.cpuUsage()` and
`process.memoryUsage()` (RSS, V8 heap and external allocations). These counters
describe the controller alone. They neither measure fork CPU nor partition
aggregate peak RSS. Profiles contain idle/native frames, so profile duration is
not CPU consumption. Synchronous diagnostic writes and inspector work perturb
scheduling and resource measurements; this run publishes no ratios or targets.
`captures.observed[].first_mutant_window` reports `requestToSendReturnMs`,
`requestToProfileAckMs` and `profileAckToSendReturnMs` separately.
The activation latency is not an exact unsampled duration:
some inspector setup is itself sampled. Complete timeline validation does not
imply complete request CPU sampling.
Any missing profile, reply, observed fork exit, worker boundary or controller
lifecycle invalidates this narrow capture. Its success makes no claim of full
process-tree profiling; startup-only captures retain their separate incomplete
lifecycle gate. Neither mode changes workload correctness/cleanup gates.
Controller-start must be the first event and controller-exit the last, enclosing
every request, reply and observed worker exit. Malformed timestamps invalidate
capture rather than preventing diagnostic-summary publication.

Controller commands require unique safe-integer IDs and alternating request/reply
events, so reordered or replayed exchanges cannot satisfy completeness. Their
mutant and baseline counts must also match the independent qualification report:
every executed mutant, one original baseline, every disabled-validation run and
any separate coverage-only run,
plus one initialize/close pair per controller. Losing an entire exchange cannot
pass by leaving a shorter, self-consistent trace. Each mutant request also records
its bounded 64-character mutant digest, with no source/test payload. The captured
IDs must exactly match the qualification report's unique executed-mutant IDs;
equal totals cannot hide a repeated mutant replacing another. Captures lacking
this identity evidence fail current validation, even if an older count-only gate
passed. Every run also records bounded `jobId` and `runId` identities. The original
baseline must match the report's `baseline` job; disabled-validation jobs must
match the CLI's single `instrumented-baseline` or indexed
`instrumented-baseline-N` names. Complete qualified coverage with no group record
marked `coverage: true` proves a separate coverage-only baseline, which precedes
the group runs and is absent from `disabledValidationRuns`; count it when deriving
these job IDs.
Every job belongs to the qualification report's run ID, and mutant job IDs must
match their mutation IDs. Repeated baselines or jobs from another run therefore
cannot satisfy reconciliation. Captures without these identities fail current
validation. Format-version-1 timelines
require contiguous event sequence numbers and a clear sticky write-failure flag;
any append failure leaves a failure marker and invalidates capture. Historical
unversioned traces have pre-send timestamps and no append-integrity attestation;
their original summaries are retained, but they fail this stronger capture gate.
Worker exit counters must report exactly one start, completion, stop and collection
response per observed fork; high-volume RPCs remain bounded first-event records.
Controller exit must be code 0. Workers must exit with code 0 and no signal, or
with Vitest's normal SIGTERM after the complete `stopped` acknowledgment.
Nonzero codes, unexpected signals and missing exit-status fields invalidate
capture. The SIGTERM case is verified in the retained native traces; requiring
code 0 for every fork would reject normal isolated Vitest shutdown.
The local inspector commands must complete synchronously before application
dispatch continues. An asynchronous inspector callback invalidates capture and
disconnects the profiler; no asynchronous waiting or redispatch of application
messages is introduced. Synchronous inspector overhead still perturbs timing as
described above. A profile is stopped only after startup has completed.

Use the `native-profile` PR label for this mode, or dispatch with
`capture_mode: controller-jobs`. Dispatch with `capture_mode: segments` to retain
the startup experiment described below. Locally, select `--controller-jobs` in
place of `--segments`; the modes are mutually exclusive to avoid inspector
sessions stopping each other's samples. Diagnostic fixture tests live beside
the preload and run with `python3 -m unittest discover -s benchmarks/profiling
-p 'test_*.py' -v` before the workflow builds or captures anything.

### Startup and exit-time captures

`Native diagnostic CPU profiles` is a separate Ubuntu 24.04 workflow. After this
change is in a PR, add the **`native-profile`** label to that PR (every push while it is
present reruns the job, so remove it after the desired capture and before
evidence-only archival pushes), or dispatch the workflow on the desired branch. PR runs record
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
for each of ordinary Vitest, Oximut and Stryker, plus a linked Oximut process
chain, matched using process identities, argv and project
cwd. The native chain, the default for the reference adapter, is the Rust worker
slot → Node controller (`m2-worker.ts`) → test forks. The Rust worker is not
captured, so the controller's parent must not be any captured identity; a
controller under a captured parent does not count. A capture that contains the
adapter's `m2-runner.mjs` is the legacy custom-adapter relay: adapter → Python
supervisor → Node controller → test forks, with no Python-free fallback. A
capture is classified as native only when the Oximut report contains the
`native coordinator: ` diagnostic and no relay runner was captured. Otherwise it
is unclassifiable and must meet the strict legacy requirements, so an uncaptured
relay can never pass as native. This is
minimum role evidence, not proof of complete process coverage.
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
Exit-time metadata writes retry transient initial failures as explicitly incomplete
records; they do not add exit/completion evidence that could hide the failure. This
also exposes short-lived processes with no profile. Segment helper loading and
invocation failures are guarded and retained as capture errors.
Explicit identity/helper failure markers also exclude any retained segments from
usable-role evidence; raw profile files remain available for inspection.


### Short-lived worker checkpoints and the first native capture

The [first native capture](https://github.com/RuiCostafrg/oximut/actions/runs/40655919509)
passed workload qualification but **failed capture validation**. Vitest ends its
forks with SIGTERM, preventing exit-time Node dumps and exit observations. The
retained Stryker worker-thread profiles also lack exit observations. This is
incomplete diagnostic evidence, not a product correctness failure or a valid
performance comparison. Raw evidence is archived under
[`2026-10-28-native-profile-incomplete`](../benchmarks/results/2026-10-28-native-profile-incomplete/README.md).

The startup capture selects explicit `--segments` mode to collect useful
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
