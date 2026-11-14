# Shared-controller batch gap, 2026-11-14

## Attribution before implementation

Started at main `7297840df3996c9e6098afbe6d37db41b294ee0b`. Read the README,
boundary-intervals.json and summarize.py on `origin/perf/per-mutant-cost-2`.
Its 234.85 ms inter-batch median is an overhead-bearing ARM diagnostic, not a
GitHub-runner estimate. A new native function-boundary capture on this ARM host
reproduces a large gap (315.76 ms median; host cost varies substantially).
This attribution justifies building on **PR #64, `perf/event-driven-handoff`,
`3c03ac4c4002f6455d537f86f85dc3866fa2e172`**, rather than duplicating its transport.
The initial branch fast-forwarded to that base. Before continuation it had been
rebased onto main `d31479b`, containing merged #64 and #65. The measured candidate
remains pre-rebase `d844d94`; these historical ratios are not measurements of
the rebased branch against the newer main. No main branch was modified by this task.

Apply `attribution.patch` to `7297840`, build the release CLI, then execute the
command in `attribution-metadata.json`. It adds CLOCK_MONOTONIC boundary marks,
with separate append-only trace files for the scheduler and native slot. It
also times the existing file/directory sync calls inside `cache::atomic`.
The patch is retained as diagnostic evidence and is **not compiled into the
implementation**. Timing includes trace append overhead; concurrent stages
can overlap. This is an Oximut-only capture, not a paired performance campaign.
Oximut completed 206 mutants: 202 killed, four survived, no errors, three
controllers / 105 jobs. The harness exits 1 because its cross-tool comparison
gate requires Vitest and Stryker rows, which were deliberately not run. No
cross-tool correctness pass or performance ratio is claimed for this capture.

Regenerate the attribution:

```sh
python3 benchmarks/results/2026-11-14-batch-gap/summarize.py \
  benchmarks/results/2026-11-14-batch-gap/trace 0 1 49 99
```

105 native jobs are original baseline, instrumented baseline, then 103 mutant
batches. The four excluded transitions are the two baseline transitions and
the two 50-job retirements (the latter include controller initialization).
`attribution.json` retains the 100 within-controller mutant intervals plus all
excluded intervals. Raw traces, measurements and the local summary are retained
compressed. The summarizer's test covers clipping and overlapping process spans.

| Native/scheduler work inside reply → next send | Median ms |
|---|---:|
| Total, within-controller mutant batches | 315.76 |
| Result envelope and semantic/identity validation | 1.26 |
| Native post-job descendant verification (`Coordinator::leaked`) | 58.22 |
| Slot return → scheduler begins reading response (file publication + observation) | 26.56 |
| Scheduler `read_response` parsing/validation | 0.82 |
| Scheduler completion sampling / dispatch return | 50.78 |
| Batch assembly, including its second forced resource sample | 47.90 |
| Assembled batch → slot reads request (dispatch, sampling, serialization, atomic publication, polling) | 94.39 |
| Slot request parsing / entry to coordinator | 6.22 |
| `Fingerprint::take` traversal, metadata and racy content hashes | 15.74 |
| Fingerprint end → next controller send (comparison, args serialization) | 1.40 |
| Resource samples anywhere in the interval (union of spans, overlaps above) | 194.83 |
| File fsyncs anywhere in the interval (overlaps hand-off above) | 42.70 |
| Directory fsyncs anywhere in the interval (overlaps hand-off above) | 10.23 |

Independent medians are not additive. Resource samples include process discovery,
identity verification, RSS, host/cgroup availability, and ownership snapshots.
Request/response file writes, serialization and polling are bounded by the
hand-off rows; fsyncs are measured separately inside them. The old capture's
adapter-send/receive boundary differs slightly from these native boundaries;
channel delivery, controller parsing and OS scheduling are not separately timed.
No work is attributed to a narrower function without a clock around it.

## Implementation decisions

- Use #64's sockets/poll/pidfd hand-off, including its nonblocking request flush.
  This removes temporary request/response files and their durability syncs from
  the hot path; durable cache/report writes retain their existing guarantees.
  #64 already pins the paired benchmark baseline to main `7297840`.
- Reuse the mandatory completion sample when assembling the immediately next
  batch, through the existing `Budget::sample_admission` policy. A broad sample
  older than 50 ms is refreshed. Every accepted response still forces an exact
  broad sample; dispatch still forces exact accounting after a counter sample;
  capacity, cgroup pressure, interruption and exhaustion gates remain in place.
- Do not pipeline fingerprints across tests: a test can modify project inputs
  while it runs. Moving the snapshot earlier alone misses those writes.
  Do not overlap leak verification with new lanes: broad ownership verification
  would see legitimate next-batch children as leaks. The measured fingerprint
  cost is secondary to accounting and transport. Streaming lanes is deferred;
  fresh-process isolation, whole-file selection and test order are unchanged.

## Validation

### Original pre-rebase candidate

- `cargo test -q --release -p oximut-cli`: 94 unit + nine integration tests passed.
- Release CLI build and the requested TypeScript typecheck: passed.
- `PYTHONPATH=tests python3 -m unittest tests.test_native_coordinator tests.test_contract`:
  27 passed, using `target/debug/oximut` linked to the candidate release binary.
- Adapter tests with `--test-concurrency=1`: all 83 passed, including shared lanes.
- Benchmark harness: initial local full run, 175 tests, had three RSS observation
  failures (`test_measurement.CommandTests`, 50 ms children exited before a live
  memory snapshot). All three passed on an isolated recheck after the adapter
  suite ended. All 175 tests passed together in each Actions campaign before
  either candidate was built. No test or measurement gate was weakened.
- `cargo fmt --all` and `git diff --check`: passed.

Commands, exit codes, and compressed logs (including the initial failures) are
retained in `validation/`. No adapter source was changed or reverted while any
test ran. Diagnostics are absent from the candidate binary.

### Rebased branch revalidation

On continuation, the branch was already based on main `d31479b` (#64 + #65).
The release CLI was rebuilt and revalidated: 94 Rust unit + nine integration
tests, TypeScript typecheck, and all 27 Python native/contract tests passed.
The adapter suite ran all 88 tests: 87 passed initially, and the lifecycle test
reported `Controller did not close`. That single test passed when run alone;
no product, timeout or test behavior was changed. Logs and command metadata
are retained under `validation/rebased/`. The full benchmark-harness suite then
passed all 176 tests in one isolated run. Formatting and whitespace checks passed.

## Paired GitHub Actions campaigns

Ubuntu 24.04 x86_64; Node 26.3.1, Vitest 5.1.0, Stryker 10.1.1. Before is
main `7297840`; after is candidate `d844d94`. Both binaries are built into isolated
target directories and copied before any timing. One warmup, five measured
pairs, alternating before/after campaign order. Each variant runs tools serially;
concurrency is matched. No diagnostic clocks or profilers are enabled. Intervals
are the harness's deterministic paired median percentile bootstrap (10,000 draws).
Five pairs offer limited tail evidence. Lower ratios are better.

### After / main

| Config / Actions run | Wall | CPU | Peak aggregate RSS |
|---|---:|---:|---:|
| colord-scale · 2 workers ([43595986333](https://github.com/RuiCostafrg/oximut/actions/runs/43595986333)) | 0.796 [0.767, 0.801] | 0.924 [0.914, 0.939] | 1.111 [1.011, 1.131] |
| colord-scale · 4 workers ([43595989528](https://github.com/RuiCostafrg/oximut/actions/runs/43595989528)) | 0.873 [0.860, 0.878] | 0.949 [0.946, 0.954] | 1.105 [1.027, 1.121] |
| colord · 4 workers ([43595993830](https://github.com/RuiCostafrg/oximut/actions/runs/43595993830)) | 0.914 [0.860, 0.948] | 0.980 [0.957, 1.008] | 0.991 [0.940, 1.048] |

### After / Stryker

| Config / Actions run | Wall | CPU | Peak aggregate RSS |
|---|---:|---:|---:|
| colord-scale · 2 workers ([43595986333](https://github.com/RuiCostafrg/oximut/actions/runs/43595986333)) | 1.292 [1.253, 1.309] | 1.086 [1.054, 1.111] | 0.923 [0.912, 0.941] |
| colord-scale · 4 workers ([43595989528](https://github.com/RuiCostafrg/oximut/actions/runs/43595989528)) | 1.185 [1.182, 1.200] | 0.985 [0.984, 0.993] | 0.806 [0.777, 0.819] |
| colord · 4 workers ([43595993830](https://github.com/RuiCostafrg/oximut/actions/runs/43595993830)) | 0.629 [0.605, 0.644] | 0.691 [0.679, 0.697] | 0.584 [0.575, 0.599] |

### Isolation: after / #64 alone (3c03ac4)

| Config / Actions run | Wall | CPU | Peak aggregate RSS |
|---|---:|---:|---:|
| colord-scale · 2 workers ([43597205960](https://github.com/RuiCostafrg/oximut/actions/runs/43597205960)) | 0.968 [0.954, 0.975] | 0.980 [0.975, 0.988] | 1.021 [0.921, 1.086] |
| colord-scale · 4 workers ([43597210750](https://github.com/RuiCostafrg/oximut/actions/runs/43597210750)) | 0.991 [0.969, 0.993] | 0.997 [0.977, 1.002] | 1.002 [0.961, 1.024] |

### Isolation campaign: after / Stryker

| Config / Actions run | Wall | CPU | Peak aggregate RSS |
|---|---:|---:|---:|
| colord-scale · 2 workers ([43597205960](https://github.com/RuiCostafrg/oximut/actions/runs/43597205960)) | 1.253 [1.241, 1.264] | 1.058 [1.052, 1.063] | 0.892 [0.852, 0.894] |
| colord-scale · 4 workers ([43597210750](https://github.com/RuiCostafrg/oximut/actions/runs/43597210750)) | 1.160 [1.144, 1.170] | 0.946 [0.939, 0.956] | 0.800 [0.792, 0.814] |

All five correctness and isolated-build integrity gates passed. Scale preserves
202 killed / four survived; colord preserves all ten killed. Summary JSON,
compressed measurements, attempts, and build metadata are retained under
`campaigns/`. `campaigns/report.json` consolidates ratios and acceptance checks.

### Acceptance and risks

**Acceptance not met.** scale 2w wall interval below one: true; scale 4w wall interval below one: true; colord wall not regressed: true; no significant cpu or rss regression: false; all correctness and build gates: true.

The first three campaigns measure #64 plus this change against original main.
The two isolation campaigns use temporary commit `4328cdc`, which changes only
the workflow baseline to #64. Its after-binary SHA-256 exactly matches `d844d94`
in all five campaigns. They compare this binary with #64 alone, and
show a smaller wall gain with no significant RSS regression. These separate
the incremental change from the combined branch. No
campaign was repeated to obtain a favorable interval. Temporary benchmark refs
were deleted after the artifacts were downloaded.

The current branch was already rebased onto main `d31479b` (merged #64 and #65)
when work resumed. The measured candidate remains pre-rebase `d844d94`; the
rebased head is locally revalidated but not retimed. No streaming lanes or speculative fingerprint/leak
verification overlap is implemented. Verification and fingerprinting remain
between batches, so some idle time remains. These results qualify only this pinned
corpus and runner class; the ARM attribution timings do not predict x86 latency.
Local 50 ms RSS observation fixtures were contention-sensitive as recorded above.
