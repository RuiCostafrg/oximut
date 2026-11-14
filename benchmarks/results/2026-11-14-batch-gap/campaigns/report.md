
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
