# W11: cheaper batch accounting, 2026-11-14

Implementation base: PR #66 head `ab6239f786a83bfbb7ee4c4faa1ab275137f7835`.
The existing checkout and four implementation commits were retained. Main was
not changed. Product behavior remains fresh-process isolation, whole-file
selection and the existing test order.

## Retained attribution

Read the prior batch-gap README, summarizer and patch before this work.
`attribution.patch` instruments #66; `attribution-candidate.patch` instruments
`34ab72d`. Neither patch is compiled into product or scored campaign builds.
`attribution-metadata.json`, `trace-before`, `attribution-before.json` and
`local-summary-before.json.gz` preserve the earlier ARM/PRoot diagnostic.
Local timing is noisy and is not performance evidence.

Completed x86_64 GitHub Actions diagnostic runs:

- [#66 attribution, 43606153267](https://github.com/RuiCostafrg/oximut/actions/runs/43606153267), instrumented after commit `ba623483d649ca5026a443a3f5658b02d443c07b`.
- [W11 attribution, 43607914768](https://github.com/RuiCostafrg/oximut/actions/runs/43607914768), instrumented after commit `be87866120f1cf2d38487f507788cf4b62277d39`.

Each trace set pools all 624 reply-to-next-controller-send intervals from
six Oximut executions (one warmup and five measured rounds at two workers),
including baseline and controller-retirement transitions. No intervals are
excluded from these CI summaries. Instrumentation appends trace records and
changes timing; these two runs are on separate runner allocations and are
**diagnostics, not a scored before/after performance comparison**. Both full
cross-tool correctness gates passed. Their summary ratios are retained only
for provenance and must not be used as W11 acceptance evidence.

| Median stage, ms | #66 diagnostic | W11 diagnostic (before EOF fix) |
| --- | ---: | ---: |
| Whole reply-to-next-send gap | 21.808 | 15.139 |
| Resource sample union | 6.639 | 9.819 |
| Discovery union (inside samples) | 1.184 | 1.412 |
| Exact RSS union (inside samples) | 5.147 | 8.093 |
| Completion sampling / dispatch return | 8.845 | 9.866 |
| Batch assembly / sampling | 2.768 | 0.167 |
| Scheduler-to-slot handoff | 5.334 | 1.748 |
| Slot request parsing | 1.636 | 0.410 |
| Descendant verification | 1.566 | 1.812 |
| Fingerprint | 0.211 | 0.268 |

Stages overlap and medians do not add. The remaining diagnostic gap is mostly
exact RSS observation, whose guarantee is intentionally retained. Final scored
candidate `3052c2b` also fixes short reads, so the pre-fix diagnostic cannot
establish its performance. Raw compressed traces, build metadata (binary hashes,
versions, SHAs), workflow logs and complete diagnostic summaries are retained.
Regenerate either summary with:

```sh
python3 benchmarks/results/2026-11-14-batch-accounting/summarize.py \
  benchmarks/results/2026-11-14-batch-accounting/trace-ci-before
python3 benchmarks/results/2026-11-14-batch-accounting/summarize.py \
  benchmarks/results/2026-11-14-batch-accounting/trace-ci-candidate
```

The summarizer precomputes span unions and indexes timestamps; its #66 output
was checked against every median in the original retained 624-interval summary.

## Changes and correctness review

- `2c56e65`: schedule the next periodic observation from a forced sample's end.
  Forced completion observations remain exact; the slow-sample test proves the
  next periodic sample waits its interval and still happens when due.
- `218db3e`: cache encoded resident-module sizes by Arc identity, retain the Arc
  to prevent address reuse, and calculate exact batch sizes without repeatedly
  serializing modules. Tests compare arithmetic to actual JSON encoding,
  including escaped/multibyte content. Existing expanded 8 MiB batch limits stay.
- `2f791af`: avoid metadata-based allocation sizing when reading procfs/cgroup
  accounting records. The original short-read optimization is corrected
  by `3052c2b` below; its historical local timing claim is not adopted here.
- `34ab72d`: transmit a lane batch's resident modules once only when every lane
  shares the identical Arc. The slot restores them before coordinator/adapter
  validation; roundtrip bytes equal the expanded original request. Distinct
  allocations keep the original transport, including equal-content allocations.
- `3052c2b`: continue pseudo-file reads through short reads until EOF. Tests cover
  interruptions, short chunks, sizes around 4096 bytes, UTF-8 and read failures.
  A short read does not prove EOF; dropping later children or resource fields
  would violate strict leak detection or accounting. [Rust 1.96 File::read_to_end](https://github.com/rust-lang/rust/blob/1.96.0/library/std/src/fs.rs) also performs its own capacity query, so the historical
  syscall-saving claim is withdrawn for the final safe reader. Keeping full
  records takes priority; the batch sizing/transport and cadence changes remain.

Self-check: `dispatch` still forces an exact sample before accepting every
ready response; budget exhaustion overrides results and stops admission.
Forced samples still do fresh broad discovery, identity verification, exact
smaps_rollup RSS and host/cgroup checks. Strict `Coordinator::leaked` discovery,
observed-PID rechecks, cleanup poisoning and deadline checks are unchanged.
No static identity caching or stale host/cgroup availability was introduced.
Nonblocking socket writes and controller deadlines retain their existing paths.
Compact native frames preserve adapter validation after restoration; malformed
compact frames with absent resident sets or pre-populated lanes are rejected.

Further shortcuts were not accepted: statm/status replace neither exact RSS nor
fresh PID identity; sampled ownership cannot prove job-end disappearance;
reusing the scheduler's observation in the slot would conflate different times
and owners. The existing subtree discovery/fallback already comes from #65/#66.
No new process-tree architecture or controller reuse experiment is included.

## Uninstrumented paired campaign

`campaign-provenance.json` records exact candidate, baseline, workflow-only
commit, run IDs and refs. Temporary commit `36c0e9b` differs from `3052c2b` only
by setting the workflow baseline to #66. It is not on the product branch.
Each job builds isolated release binaries before timing, disables diagnostic
instrumentation, runs one warmup and five measured repetitions with matched
parallelism, and retains full mutant-scope/cross-tool correctness gates, CPU,
peak RSS, raw measurements and bootstrap uncertainty intervals.

| Workload | Workers | Run | Status |
| --- | ---: | --- | --- |
| colord-scale | 2 | [43621387049](https://github.com/RuiCostafrg/oximut/actions/runs/43621387049) | Correctness passed; complete |
| colord-scale | 4 | [43621390731](https://github.com/RuiCostafrg/oximut/actions/runs/43621390731) | Correctness passed; complete |
| colord | 4 | [43621394309](https://github.com/RuiCostafrg/oximut/actions/runs/43621394309) | Correctness passed; complete |

Both scale wall-ratio 95% intervals lie below 1.0: the measured scale wall
improvement target is met at both worker counts. CPU and RSS show no regression
beyond noise; small colord is consistent with no change. This satisfies the
requested improvement check with no detected regression in the other measured
metrics. It does **not** prove strict noninferiority: small-colord wall's upper
bound is 1.0391, scale-4w CPU's is 1.0162, and scale-2w RSS's is 1.0248. With
five pairs, modest regressions remain compatible with those intervals.

All three scored runs passed the full correctness and required build-integrity
gates. The exact colord-scale scope is 206 mutants across 20 declared arithmetic
mutation files, with all 41 source files and all 102 upstream tests available;
small colord has 10 mutants across five declared files. This is host/corpus-
specific evidence, not a full-colord or general performance guarantee.

Against Stryker, scale wall time remains slower: 1.2199 at 2w and 1.1674 at 4w;
W11 does not close that gap. The retained pre-fix diagnostic identifies exact RSS
sampling as the largest remaining component, but does not justify weakening it.

## Validation

Logs in `validation/` record this continuation's checks, with Cargo build jobs
limited to three. Release CLI tests (99 CLI + 11 integration) and release clippy with `-D warnings` passed.
Native-coordinator/contract Python tests use Node v26.3.1; they require a current
`target/debug/oximut`, so a debug build precedes them. The 27 native-coordinator/contract tests passed. The full benchmark suite
ran 176 tests with two local failures: peak-capture timing produced no snapshot
and a terminated worker had no finalized partial profile. Targeted recheck with
the pinned Node PATH passed the partial-profile test but reproduced the absent
peak snapshot. Both failures are retained in logs; the same complete harness
suite passed on all three scored Ubuntu jobs before measurements. No unrelated
harness changes were made. No local benchmark
performance claim is made.

## Completed scored ratios

Ratios below are median [bootstrap 95% interval]; lower is better. Raw artifact
archives, full summary JSON and build metadata are under `campaigns/`.

| Config | Comparison | Wall | CPU | Peak aggregate RSS | Gate |
| --- | --- | --- | --- | --- | --- |
| colord-scale 2w | after_vs_before | 0.9832 [0.9533, 0.9918] | 0.9932 [0.9698, 0.9991] | 1.0064 [0.9080, 1.0248] | Passed |
| colord-scale 2w | after_vs_stryker | 1.2199 [1.2091, 1.2274] | 1.0424 [1.0338, 1.0492] | 0.8660 [0.8526, 0.8917] | Passed |
| colord-scale 4w | after_vs_before | 0.9744 [0.9092, 0.9936] | 0.9940 [0.9270, 1.0162] | 0.9641 [0.9503, 1.0080] | Passed |
| colord-scale 4w | after_vs_stryker | 1.1674 [1.1451, 1.1993] | 0.9683 [0.9535, 0.9952] | 0.7720 [0.7420, 0.7849] | Passed |
| colord 4w | after_vs_before | 1.0001 [0.9813, 1.0391] | 1.0013 [0.9877, 1.0271] | 0.9898 [0.9594, 1.0410] | Passed |
| colord 4w | after_vs_stryker | 0.6335 [0.6251, 0.6543] | 0.7059 [0.7011, 0.7228] | 0.5941 [0.5683, 0.6132] | Passed |

Each `campaigns/*/raw-artifact.tar.gz` preserves the entire downloaded artifact,
including raw per-round measurements, logs, reports, scope checks and build
metadata. `summary.json`, `build-metadata.json`, `artifact-api.json`,
`workflow.log.gz` and `raw-inventory.json` are also retained separately. Inventories
contain per-file SHA-256 values and the complete archive hash. Both scale
archives were checked byte-for-byte against all 2,129 downloaded files before
ref cleanup. Paired medians were checked against the five stored ratios; recorded
SHAs, repetitions, worker counts, empty extra args and gates were also checked.

No product implementation changed during evidence recovery; no builds or local
benchmarks were run. Product code remains the independently reviewed `be2a344`.
No PR was opened and main was not touched. The next step is parent review of the
implementation and this evidence, not another scored campaign selected by result.

Both `w11-2w` and `w11-4w` were deleted only after complete artifact recovery
and archive verification. The temporary baseline-override commit remains outside
the product branch. The small/diagnostic refs were already deleted in the prior
continuation. `wall-ratios.html` plots the scored wall intervals versus #66.
HTML preview could not run on this host because T3 browser libraries are missing;
no browser setup or dependency installation was performed for evidence recovery.
