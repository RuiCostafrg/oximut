# Per-mutant cost, 2026-11-14

Base: `7297840df3996c9e6098afbe6d37db41b294ee0b` (origin/main, #63).
A single local ARM/Android-host capture, Node 26.3.1, Vitest 5.1.0,
Stryker 10.1.1, two workers, no warmup:

```sh
python3 benchmarks/profile_native.py --binary target/release/oximut \
  --controller-jobs --workload colord-scale --concurrency 2 \
  --output /tmp/oximut-w9-profile-controller-2
```

The supplied profiling patch adds workload-specific qualification: scale requires
206 outcome-equivalent edits, including survivors; colord still requires ten kills.
The first preflight attempt used an old dependency directory and ran no workload;
the capture above uses the pinned dependencies. Profiling took under ten minutes,
including a release build. These are diagnostic boundaries with profiler, synchronous
trace-write and external resource-sampler overhead, **not scored performance ratios**.

| Boundary (independent medians, ms) | ms |
|---|---:|
| Request observed → fork creation | 44.38 |
| Fork creation → worker started (Node + Vitest bootstrap) | 369.69 |
| Started → run dispatch | 0.79 |
| Run dispatch → first collection RPC (initial module import/transform + collection) | 257.58 |
| First collection RPC → first testfileFinished (execution + result reporting) | 86.90 |
| testfileFinished → process exit | 18.69 |
| Last first-file finish → controller reply | 20.08 |
| First-file finish skew within a batch | 91.93 |
| Controller reply → next batch request, within a controller | 234.85 |
| Whole batch request → reply | 795.89 |

206 fresh forks, 103 batches, three controllers (50-job retirement).
Workload qualification passed: 202 killed, four survived, matching Stryker exactly;
complete baseline attribution. **Strict capture validation failed**: eight multi-file
workers sent multiple collection/finish messages, and the third controller's short
CPU sample failed profile validation. No capture was repeated to manufacture a pass.
All 206 fork/start/first-collection/first-finish/exit boundaries were observed; the
derived table is partial diagnostic evidence only. See [diagnostic.json](diagnostic.json).
See [boundary-intervals.json](boundary-intervals.json) for every measured interval.
These medians overlap and must not be added. The preload observes IPC invocation
and receipt, not recipient delivery or callback completion. Import/transform and
test collection cannot be separated by these boundaries; similarly execution includes
reporting. Multi-file workers can continue importing/running files after the first
finish, so the finish-to-reply and finish-to-exit intervals also include that work. There is no fabricated finer partition. The first-mutant controller CPU
profile is predominantly idle; it excludes worker CPU and includes profiler activation.

The dominant worker-side boundaries justify sharing the controller with opt-in fresh
threads. Batch finish skew and native verification/fingerprinting between requests are
also visible, but this host's syscall costs are not representative of the GitHub runner.
Removing those checks would weaken correctness; a streaming scheduler is a separate,
larger change. No additional default runtime tweak is justified by this short capture.

Hit-only selection is deferred. The existing duplicate-name/state-observer regression
executes the mutation in one test and detects it only in a later test. Complete hit
attribution cannot prove independent tests. Whole-file selection and test order remain
unchanged per the owner's direction. A sound opt-in implementation needs changes to
Rust selection, request validation, adapters, inventory checks, and end-to-end tests;
that exceeds the requested roughly 150-line small follow-up budget.
