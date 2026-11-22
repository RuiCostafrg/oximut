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
| First collection RPC → worker finished (execution + reporting; later file imports if any) | 86.90 |
| testfileFinished → process exit | 18.69 |
| Last lane finished → controller reply | 20.08 |
| Lane finish skew within a batch | 91.93 |
| Controller reply → next batch request, within a controller | 234.85 |
| Whole batch request → reply | 795.89 |

206 fresh forks, 103 batches, three controllers (50-job retirement).
Workload qualification passed: 202 killed, four survived, matching Stryker exactly;
complete baseline attribution. **Strict capture validation failed**: eight multi-file
workers sent multiple collection messages, and the third controller's short
CPU sample failed profile validation. No capture was repeated to manufacture a pass.
All 206 fork/start/first-collection/worker-finish/exit boundaries were observed; the
derived table is partial diagnostic evidence only. See [diagnostic.json](diagnostic.json).
See [boundary-intervals.json](boundary-intervals.json) for every measured interval,
including a separate summary for the 198 single-file workers. Raw timelines are
retained compressed in `timelines/`; regenerate with
`python3 benchmarks/results/2026-11-14-per-mutant-breakdown/summarize.py benchmarks/results/2026-11-14-per-mutant-breakdown/timelines`.
These medians overlap and must not be added. The preload observes IPC invocation
and receipt, not recipient delivery or callback completion. Import/transform and
test collection cannot be separated by these boundaries; similarly execution includes
reporting. Eight multi-file workers continue importing/running files after the first
collection RPC; their execution interval includes that additional work. There is no
fabricated finer partition. The first-mutant controller CPU
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


## Local validation of implementation `1620854`

- `cargo test -q --release -p oximut-cli`: passed (78 + 9 tests).
- `cargo build -q --release -p oximut-cli`: passed; the Python suite's
  `target/debug/oximut` path points to that release binary for this validation.
- `PYTHONPATH=tests python3 -m unittest tests.test_native_coordinator tests.test_contract`:
  27 passed. Without `PYTHONPATH=tests`, an existing absolute sibling import in
  `test_contract` fails to find `test_m3_failures`; no product failure.
- `node node_modules/typescript/bin/tsc -p packages/vitest-adapter/tsconfig.json`: passed.
- Adapter tests: 73 passed with `--test-concurrency=1`, plus all 13 shared-lane
  tests passed in a separate run. The new thread tests cover overlap, fresh globals,
  module reset between files, local bail, async timeout, synchronous infinite loops,
  termination of every captured worker handle, smaller batches, and controller routing.
- `python3 -m unittest discover -s benchmarks/tests`: 175 passed.
- `cargo fmt`, adapter Biome formatting, and `git diff --check`: passed.

The initial new thread test left Vitest's expected failure exit code on the host
process; its cleanup now resets it, and the complete shared-lane suite passes.

## Paired GitHub Actions campaigns

Ubuntu 24.04 x86_64; Node 26.3.1, Vitest 5.1.0, Stryker 10.1.1. Before is main `7297840`; after is implementation `1620854`. One warmup, five measured rounds, serial before/after campaigns per round. Diagnostic clocks and profilers are off. Intervals are the harness's deterministic paired median percentile bootstrap (10,000 draws), with only five measured pairs. All values are ratios; lower is better. CPU is product process-tree CPU; RSS is peak aggregate live RSS. Each cell is median [95% interval].


### After / main

| Config / run | Wall | CPU | Peak RSS |
|---|---:|---:|---:|
| scale-2w-threads ([43593395274](https://github.com/RuiCostafrg/oximut/actions/runs/43593395274)) | 0.947 [0.900, 1.001] | 0.937 [0.933, 0.942] | 0.755 [0.715, 0.800] |
| scale-4w-threads ([43593398752](https://github.com/RuiCostafrg/oximut/actions/runs/43593398752)) | 0.973 [0.944, 0.984] | 0.947 [0.925, 0.951] | 0.721 [0.675, 0.779] |
| colord-4w-threads ([43593401959](https://github.com/RuiCostafrg/oximut/actions/runs/43593401959)) | 0.960 [0.946, 0.969] | 0.948 [0.938, 0.957] | 0.674 [0.667, 0.738] |
| colord-4w-default ([43593404492](https://github.com/RuiCostafrg/oximut/actions/runs/43593404492)) | 1.000 [0.979, 1.188] | 1.001 [0.983, 1.024] | 1.023 [0.995, 1.038] |

### After / Stryker 10.1.1

| Config / run | Wall | CPU | Peak RSS |
|---|---:|---:|---:|
| scale-2w-threads ([43593395274](https://github.com/RuiCostafrg/oximut/actions/runs/43593395274)) | 1.662 [1.551, 1.699] | 1.103 [1.101, 1.110] | 0.610 [0.604, 0.668] |
| scale-4w-threads ([43593398752](https://github.com/RuiCostafrg/oximut/actions/runs/43593398752)) | 1.232 [1.204, 1.245] | 0.922 [0.900, 0.926] | 0.575 [0.531, 0.592] |
| colord-4w-threads ([43593401959](https://github.com/RuiCostafrg/oximut/actions/runs/43593401959)) | 0.597 [0.590, 0.606] | 0.638 [0.630, 0.644] | 0.401 [0.390, 0.430] |
| colord-4w-default ([43593404492](https://github.com/RuiCostafrg/oximut/actions/runs/43593404492)) | 0.635 [0.623, 0.673] | 0.686 [0.675, 0.701] | 0.595 [0.588, 0.613] |

All four correctness gates and isolated-build integrity gates passed. The scale workload keeps 202 killed / four survived; colord keeps all ten killed. Raw summaries, compressed measurements, and build metadata are retained under `campaigns/<config>/`. Temporary benchmark refs were deleted after downloading the artifacts.

### Acceptance and remaining limits

Full acceptance is **not met**: the 2-worker scale wall interval reaches
1.001, so its 5.3% median wall reduction is inconclusive. The 4-worker scale wall
interval is wholly below one (2.7% median reduction). Both scale runs reduce CPU
and RSS with intervals wholly below one. Neither small-workload configuration
shows a significant wall, CPU or RSS regression. No campaign was repeated to
force a favorable interval.

On scale, fresh threads still take 1.662x Stryker wall time at two workers and
1.232x at four; this change does not achieve the broader goal of beating Stryker.
The useful result is shared-controller thread support with materially lower
memory and CPU than main. Keep it opt-in.

Fresh-process isolation, whole-file selection, and in-file test order remain unchanged. Thread isolation stays opt-in. Threads share the controller process, so native crashes can terminate all its lanes and process-global native state is not isolated. Only this pinned corpus and host are qualified. Hit-only selection and a streaming lane scheduler remain follow-ups.
