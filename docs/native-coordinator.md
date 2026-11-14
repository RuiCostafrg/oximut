# Native execution coordinator: design and PR plan

Status: P1 (#29) and P3 (#30) are merged. P4 (#31) is parked as a draft: its
gain was not significant once P1 had landed. P2 (the native coordinator, #32) is
under measurement.

Status: proposal plus incremental implementation plan. Each step below ships as
its own PR from `main` and is measured by the native colord before/after
campaign (`real-project-benchmark.yml`). Nothing in this document is a
performance claim; only qualified native campaign evidence is.

## Goal

On the pinned colord workload (41 sources, 102 tests, five mutated files, ten
killed arithmetic mutants, one worker), against tuned Stryker. These targets
supersede the campaign harness's current thresholds (wall ≤ 0.80, RSS ≤ 0.50,
no CPU target) for this plan. A dedicated PR aligns `compare_candidates.py`'s
`targets_met` with them before any target claim.

| Metric | Target (paired ratio, CI upper bound) | `main` @ 988a619 (run 41182865902) |
|---|---|---|
| Wall time | ≤ 0.50 | 2.41 (2.23–2.50) |
| CPU seconds (user+system, process tree) | ≤ 0.80 | 3.26 (3.13–3.42) |
| Peak aggregate sampled RSS | ≤ 0.80 | 0.75 (0.74–0.76) |

Baseline provenance: run 41182865902 (pull_request event for #24; measured
merge revision `0b15f6c`, tree identical to `main` @ 988a619; before candidate
`7a2c850`). Ubuntu 24.04 x86_64 Azure runner; Node v26.3.1, Vitest 4.1.12
(lockfile, enforced by the harness), Stryker core/vitest-runner 10.0.0, Rust
1.96.0, Python 3.13.17. One warmup and five measured pairs.

All existing correctness gates stay fail-closed: the original baseline oracle,
inactive-instrumentation validation, exact test inventory, deadlines, verified
descendant cleanup, cache invalidation and result validation.

## Where the time goes today

Raw medians (ms) from the five measured rounds: `disabledSwitchValidation` 4430,
`mutationExecution` 3687, `originalBaseline` 1646, `coverageBaseline` 1107,
`teardown` 89. Everything else is near zero. Oximut walls 10.8 s and burns 19.6 CPU s;
plain Vitest needs 1.3 s / 1.7 s for one full run.

Process chain per run (6 processes plus forks):

```text
oximut CLI ─ oximut --internal-worker ─ node m2-runner.mjs ─ python3 supervisor.py ─ node m2-worker.ts (Vitest) ─ fresh fork per test file
```

The startup-qualified profile capture and the code identify the costs:

1. **38 fresh Vitest forks per run.** `isolate: true` + `pool: forks` spawns
   a process per test file per job: original baseline 4, disabled-switch
   validation 5 × 4 = 20, coverage baseline 4, mutants 10. Each fork boots Node,
   compiles and evaluates the Vitest worker runtime, then fetches transforms over
   RPC (about 220 ms wall per fork on the hosted runner). `compileSourceTextModule`
   was the top leaf in fork startup samples.
2. **Validation reruns.** Each switched file gets its own full-suite disabled
   run although all mutants of a file share a single switched transform. The
   coverage baseline then runs the suite again with a *different* (probe-only)
   transform. So the code that mutants execute is validated file-by-file, and the
   coverage run validates a third variant.
3. **Orchestration overhead per job (about 100+ ms).** Requests and responses are
   files polled by three layers (CLI every 10 ms, internal worker 10 ms,
   m2-runner 15 ms). Before every job, m2-runner walks and `stat`s the whole
   project to decide controller reuse. After every job it enumerates `/proc` up
   to three times to prove no test descendant leaked.
4. **Process accounting in five places.** The CLI enumerates *all* of `/proc`
   on every 10 ms dispatch poll (`Worker::ready` → `snapshot`), plus the 50 ms
   resource sampler with `smaps_rollup`. Add the internal worker every 50 ms, the
   Python supervisor (about 2.8 s profiled self-CPU, 37k identity reads) and the
   JS runner per job. On a host with hundreds of processes this is real system CPU.
5. **Two interpreter layers that only relay.** `m2-runner.mjs` (a Node process,
   about 40 MB RSS) and `supervisor.py` (subreaper and verified cleanup)
   duplicate work the Rust worker already does: it is a subreaper, it tracks
   ownership and it enforces deadlines.

Stryker, by contrast: one instrumented dry run (no uninstrumented oracle),
`pool: 'threads'` overriding the project's `forks`, mutants switched at runtime
through a global, and `bail: 1`. Because the project sets `isolate: true`, Vitest
5.1.0's pool stops each runner after its test file and never returns it to the
shared-runner list, so every test file still gets a fresh worker *thread*.

## Honest reading of the proposed architecture

The proposal is: a Rust-native mutation engine (Oxc, scheduler, cache,
coverage mapping), then a persistent native execution coordinator, then a minimal
Vitest compatibility layer. Moving orchestration from Node/Python into Rust
reduces costs 3–5: it removes the two relay processes, file polling and
duplicate scanners, and consolidates the remaining process scans and
fingerprint walks into Rust. P2 still keeps a stat walk and cleanup checks,
and P3 keeps a full `/proc` enumeration for cleanup verification, so scan
costs shrink but do not disappear. It does **not** by itself remove fresh test workers,
which are costs 1–2 and the majority of wall time. Those need separate changes:

- **Run fewer suites.** Validate the exact code mutants will execute, once, in
  the same run that collects coverage ("instrument once").
- **Make each fresh worker cheaper.** Use a compile cache for the Vitest runtime,
  and offer an isolation mode equivalent to what tuned Stryker uses (a fresh
  worker thread per test file) as an explicit, reported choice.

Keeping workers alive across mutants (no fresh realm per mutant) is out of scope:
it would weaken the isolation guarantee and is not needed to match Stryker's
semantics.

## Target architecture

```text
Rust: oximut CLI (Oxc instrumentation, scheduler, cache, coverage mapping,
      resource admission, single process accountant)
  └─ Rust: worker slot = subreaper + native coordinator
       (pipe protocol, deadlines, pidfd-verified cleanup, leak checks)
        └─ node: Vitest compatibility layer (createVitest, transform plugin,
                 run/collect/coverage reporter; no orchestration logic)
             └─ fresh isolated test worker per test file (fork or thread)
```

## PR plan (one improvement per PR, each measured)

Each PR runs the native before/after campaign and reports wall, CPU
(user+system), peak aggregate RSS, process counts and per-phase timings, with
paired Oximut/Stryker and after/before ratios and bootstrap 95% CIs. When a
merged step makes the pinned baseline uninformative, `BEFORE_COMMIT` is bumped
deliberately in its own commit with the reason.

### P1 — Instrument once: one validated instrumented baseline

- Oxc emits a combined transform per switched file:
  `(process.env.OXIMUT_ACTIVE_MUTANT === "id" ? (replacement) : (globalThis.__oximut_m3_hit__?.("id"), (original)))`.
  Files where switching falls back (static mutants) keep probe-only coverage
  code. Files that cannot carry probes (e.g. `globalThis` use) keep switch-only
  code and mark coverage incomplete, as today.
- One **instrumented baseline** serves the combined code for *all* switched files
  with no active mutant. It must pass with test counts identical to the original
  baseline, the same check the per-file disabled runs applied. This validates
  the transforms and their *inactive* paths. Active paths run only in mutant
  jobs; their equivalence with static mutants stays covered by the switching
  oracle tests. With test selection on, the same run collects per-test coverage.
  That coverage still has to pass the existing inventory and identity
  validation; anything incomplete falls back to full selection, never to
  narrower selection.
- Every mutant run serves the same resident transforms (only the active-mutant
  selector changes; static mutants override their own file). The executed code
  therefore equals the validated code, and Vite's transform cache is no longer
  invalidated per mutant.
- The original uninstrumented baseline stays as the oracle.
- Expected effect: 38 → 18 fresh forks; removes most of the 4.4 s validation
  phase. Report schema: `phaseWallMs.instrumentedBaseline` replaces
  `disabledSwitchValidation` + `coverageBaseline`; `disabledValidationRuns`
  becomes one record listing every validated file. The same PR updates the
  campaign integrity gate (`compare_candidates.py`) to accept this record while
  still requiring each declared file to be validated exactly once.

### P2 — Native coordinator: Rust drives the Vitest layer directly

- The Rust worker slot spawns the Node controller itself, talking
  length-bounded NDJSON over a dedicated channel (no stdout/stderr sharing, no
  file polling). `m2-runner.mjs` and `supervisor.py` leave the execution path.
- The protocol contract stays the same: requests of at most 8 MiB and
  responses of at most 1 MiB, enforced on both sides. The Node layer keeps
  `validateRequest`, and Rust keeps `Response::validate` (identity, generation,
  staleness). Conformance tests cover the limits and rejection paths before the
  old path is removed.
- The Rust slot is the subreaper and does verified cleanup with
  pidfd-pinned identities, the same guarantees as `supervisor.py`. Ports:
  post-job leak detection with independent disappearance checks, deadline-bounded
  shutdown, controller recycling, crash retry policy and input-change detection
  for controller reuse. The reuse check keeps today's per-input path, size,
  mtime and ctime comparison, and any symlink still forces a fresh controller.
  It also closes a gap that check has today. Filesystems with coarse timestamps
  can leave all three unchanged after an equal-size rewrite in the same tick.
  So, following git's "racily clean" rule, any entry whose mtime or ctime is not
  strictly older than a snapshot's start minus a 2-second precision allowance is
  content-hashed in that snapshot and again in the next one. A hash change
  between them retires the controller. A later write to an entry that was not
  racy moves its ctime past the recorded value, so metadata alone detects it.
  Two seconds covers FAT, the coarsest common granularity. A regression test rewrites a file with the
  same size and restored timestamps. The check is strictly stronger than
  today's, never weaker. Content-hashed cache fingerprints stay where they
  apply today: in cache-enabled runs, after the baseline and before
  publication.
- Blocking `poll(2)` with deadlines replaces the 10/15 ms sleeps.
- Expected effect: two fewer processes (CPU and RSS), about 100 ms less per job.

### P3 — One process accountant

- One sampler in the CLI feeds both resource admission and cleanup ownership
  snapshots, instead of a full `/proc` scan on every 10 ms dispatch poll.
- Periodic accounting walks the owned tree (each subreaper's `children` links,
  with a full enumeration fallback). **Cleanup verification keeps a full fresh
  `/proc` enumeration**, so the fail-closed cleanup gate is unchanged.
- Expected effect: lower system CPU and less sampling jitter on the wall clock.

### P4 — Compile cache for the Vitest layer and workers

- Set Node's `NODE_COMPILE_CACHE` to a per-run private directory (removed at
  teardown) for the controller and its test workers. V8 validates cached code
  against source hashes and flags, so semantics do not change. This cuts the
  repeated compile of the Vitest worker runtime in every fresh worker.

### P5 — Isolation mode: fresh worker thread per test file (opt-in, measured)

- `--isolation fresh-process` (current default: fresh fork per test file) vs
  `--isolation fresh-thread` (fresh worker thread per test file, `isolate: true`
  kept). Fresh-thread matches the semantics tuned Stryker uses on this benchmark.
- The report declares the isolation mode. Controller-level checks reject
  configurations the mode cannot isolate (for example `process.chdir`, or
  `process.exit` interception differences documented by Vitest).
- Measure both modes. Whether the benchmark or the default uses fresh-thread is
  an explicit decision recorded with that evidence, never an implicit flip.

### Stretch levers (only if P1–P5 miss the target; measured individually)

- Pipelined worker start: prepare the next isolated worker while the current
  one finishes. This trades RSS headroom for wall time.
- Selector hoisting: read the active mutant once per module evaluation instead
  of on every switched expression. This is valid because each worker is fresh.
- Avoid re-reading test files per job for the environment pragma (cache by
  content hash).

## Parallel persistent baselines

For persistent baselines, the scheduler forwards a `workers` limit capped by
both effective CPU concurrency and the current memory admission capacity (and
protocol maximum 1024). Before any worker cost is measured, the first baseline
assumes 256 MiB per fork and always keeps at least one. Serial dispatch still admits only one baseline job;
its internal fresh workers consume that admitted capacity. Aggregate RSS and
host/cgroup pressure sampling still cover the entire slot process tree and
abort on exhaustion. The original and instrumented baseline oracles, inventories,
deadline, worker termination and post-job descendant cleanup gates are unchanged.
With one controller per worker, mutant jobs retain one test worker per controller;
the shared controller below instead runs one per lane.

## Spare controller warm-up

Persistent runs with more than one admitted worker and a nonempty initial mutant
inventory prepare spare slots during original baseline dispatch. Baseline-only
runs prepare no spares. Resident groups are planned before that dispatch;
spares initialize with the first group's instrumented modules, then receive a
private `warm` command. The command globs test specifications and recursively
requests Vite SSR transforms of resident modules, test files and their local
import graphs. It never collects tests, imports user modules or starts a test
pool. Each mutant still gets fresh isolated workers. Other resident groups and
static overrides retain the normal invalidation behavior.

Warm-up retains scheduler memory admission, aggregate RSS accounting, deadlines,
ownership discovery, leak verification and shutdown. Pending spares stay separate
from the real dispatch queue. In-flight warm jobs occupy their slot until their
acknowledgement arrives, but do not count as active real jobs, consume real-job
CPU capacity or block serial baseline admission. Baseline dispatch returns once
its real job completes; later dispatch loops accept warm acknowledgements. Thus
the instrumented baseline can use its full admitted fork limit while spares warm.
Mutants use ready slots and wait when all remaining slots are still busy warming;
a warming slot never starts a duplicate controller.

Refused admission skips remaining spares. A warm or spare initialization failure
is diagnostic only: its slot is aborted and may start lazily for a mutant. Global
exhaustion or interruption still stops every slot and fails closed. Shutdown
writes stop to all normally closing slots before polling their exits together,
with an independent seven-second deadline per slot, unchanged exit/ownership checks
and final global subreaper cleanup. In-flight warms are abandoned immediately
through verified abort; completed warms close normally and retain their diagnostics.
Single-worker runs and relay adapters send no warm command. Warm acknowledgements
carry zero tests and never enter baseline or mutant results.

## Shared controller (default for parallel persistent runs)

With at least two admitted workers, persistent execution and the native adapter,
mutants run in one controller. Lanes are named Vitest
projects; each mutant still gets one fresh OS process for all its selected
files, and stop-at-first-failure applies per mutant. `--no-shared-controller`
selects one controller per worker. With `--isolation fresh-thread`, each mutant
instead gets one fresh worker thread for all selected files inside the same shared
controller. The pool drains and awaits every thread's termination before lane
reassignment, including hard deadlines; test order within a file is unchanged.
This remains opt-in and has the thread-mode limitations described in [m2.md](m2.md).
Projects whose controller can't be shared
(custom plugins or runners, global setup, non-Node test files) fall back to one controller
per worker and record a diagnostic; configurations that persistent execution
already rejects, such as multiple projects, are still rejected.

The process boundary differs from one controller per worker, where every selected
test file gets its own fork. Here a mutant's selected files share its one fork:
Vitest still resets modules between them, but process-level state such as patched
globals or native addon state can carry from one file to the next. Use
`--no-shared-controller` when tests depend on a process per file.

Lanes share module transforms: `transform-share.ts` wraps each lane project's
`ModuleTransformService.fetch` so one lane transforms a file and every other lane
is served that result (single flight, keyed by pipeline fingerprint, Vite
environment, tmp-copy mode, url, start offset and, for non-absolute urls, the
importer; the fingerprint covers the resolved configs including plugin behavior,
and a config too deep to serialize turns sharing off). Only plain fetches of files
under the project root or the lane runner's directory are shared; externalized modules, `node_modules`, errors, cache-validation fetches and
results whose tmp copy vanished go to the lane's own service. The share is cleared
whenever `SharedLanes` invalidates the module graphs (changed resident modules),
on close and on every new context, and `fsModuleCache` stays off.

This depends on
private details of Vitest 5.1.0; `shared-lanes.ts` refuses any other version.

## Release or follow-up

If the qualified campaign meets all three targets with every gate passing,
release through the private Homebrew tap, mirroring ts-crap's tap and release
distribution and reusing the R1–R3 source-release and provenance tooling.
Otherwise a dedicated follow-up thread explores out-of-the-box reductions, driven
by profiles of where time and memory actually go.
