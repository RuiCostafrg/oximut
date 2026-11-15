# W13 scored evidence: INVALID / INCOMPLETE

[Native run 43669808238](https://github.com/RuiCostafrg/oximut/actions/runs/43669808238) completed with **failure**, at exact candidate `88b7d6712648979dc7438a4044abf0f63650d463` versus stock `5dfd17e53cd7b067586b3ad47363cb3ecee5873e`. No new dispatch or retry followed this failure. Qualified adapter, bundle, helper, validation, diagnostic and scoring semantics are unchanged during recovery.

**Wall, CPU and RSS mission results are all unassessed.** The mandatory correctness gate failed, so every global comparison and acceptance is suppressed. No after/before or candidate/Stryker ratios, bootstrap intervals, speedup claim or scored HTML chart are published. This is not evidence that a numerical performance threshold was met or missed. It is a stop for this scored candidate until its concrete correctness/infrastructure failure is explained. The earlier bounded native passes remain historical facts, not proof of reliability across repeated trials.

## Concrete failure

The scale206/workers4 **warmup**, round0, candidate Oximut invocation exited 2. Its report is incomplete: 206 mutants, 199 killed, 3 survived and 4 infrastructure-error. Each bad mutant contains `Unattributed batch error`. The same-round stock invocation completed with 202 killed and 4 survived; for these four exact IDs its outcomes are three killed and one survived. All other 202 classifications match stock. Both original and instrumented baselines passed with 102 tests and complete attribution/disabled-validation ledgers. Requested/effective workers remain 4, admission pauses 0, resource exhaustion absent, and the external harness records no deadline, memory-read error or descendant-cleanup intervention.

| Exact mutant ID | Source location | Stock | Candidate | Candidate tests passed/failed |
|---|---|---|---|---|
| `08a202fd42efe7dcacc1c6b15c952582dd74901cf9baecca9eaa81a5c098a58d` | `src/colorModels/cmyk.ts:45:15` | killed | infrastructure-error | 5/1 |
| `9ac8a011bc1502b93d38fb1ff9c1d9d64b3dfff020283c5ae41da91473b75fc8` | `src/colorModels/cmyk.ts:51:31` | killed | infrastructure-error | 5/1 |
| `c8fb72bec90d9a783caf7ef8479772865cadc5338cbace358a9a173db7fcf175` | `src/manipulate/invert.ts:6:10` | killed | infrastructure-error | 26/1 |
| `4c4dd13e31689434138c647f612e775284ce81b1544891e102cfd99539e2a1d0` | `src/colorModels/cmykString.ts:19:25` | survived | infrastructure-error | 0/0 |

Trace evidence records 206 `validated-fork-artifact` events, 205 distinct bundled `fork` events and 205 `run` dispatches. Map registration also records 205 workers. Two actual controllers each completed full validation, preserving main’s existing recycling. Selector `4c4dd13e31689434138c647f612e775284ce81b1544891e102cfd99539e2a1d0` has no recorded fork/dispatch and zero completed tests. The other three bad selectors have recorded fork/dispatch evidence and assertion errors, but the existing fail-closed aggregate error rule taints their batch. No outcome is relabeled or ignored.

The retained report does not expose the underlying start/aggregate unhandled error. The trace localizes a gap between artifact configuration/validation and recorded fork/start dispatch; it does **not** prove whether the failure arose in spawning, a start check, process inspection, the native pool, or another aggregate path. No root cause, collector duplication or source-map failure is inferred from the generic message. The preexisting per-lane deadline and aggregate error rules were left intact.

Raw failure path inside `artifact.zip` → `w13-score-raw.tar.gz`: `raw/colord-scale-4w/round-0/after/oximut/{report.json,observation.json,output.log,prepared/integration-events.jsonl,prepared/map-events.jsonl}`. [Readable exact failure projection](native-43669808238/failure-detail.json).

## Completed and incomplete gates

| Configuration | Tool observations / required | Full preparations / required | Completed warmup + measured pairs | Gate |
|---|---:|---:|---|---|
| colord-2w | 36/36 | 6/6 | 1 + 5 | configuration passed; global acceptance suppressed |
| colord-4w | 36/36 | 6/6 | 1 + 5 | configuration passed; global acceptance suppressed |
| colord-scale-2w | 36/36 | 6/6 | 1 + 5 | configuration passed; global acceptance suppressed |
| colord-scale-4w | 5/36 | 1/6 | warmup stopped; 0 measured pairs | candidate exit2; correctness/infrastructure failure |

Whole campaign: 113/144 tool commands,19/24 complete external preparation commands,19/24 unique artifact owners and 1561/2592 recorded distinct bundled fork PIDs. The expected full matrix was not reached; no pass is claimed for missing rows. The first three configurations each passed their existing exact source-edit/classification/inventory/phase/build/resource gates. The fourth failed in its first warmup, before its candidate-group Stryker command and all five measured pairs. No invalid first-attempt rows or artifacts were reused.

The native global `campaign-summary.json` clears all comparisons and target acceptance, including the three completed configurations. Original per-configuration preliminary evaluator output remains unmodified **inside the complete raw archive**; it is not a valid published campaign result. Recovery did not execute the retained success-only `verification-source.py`, invoke a scored evaluator to obtain replacement ratios, run a product command, relax a gate or rerun CI.

## Raw resources: diagnostic observations only

All full observations are retained in the archive and [compressed raw observation export](native-43669808238/raw-observations.json.gz). The table below is descriptive absolute data for completed configurations, not a qualified performance comparison. Each row has five measured observations; cells give median(min–max). RSS is MiB, wall/CPU seconds. First-use values are formed per invocation by adding raw setup and execution wall/CPU and taking MAX of their sequential RSS peaks **before** computing these descriptive medians. Nothing is subtracted; Rust’s peak is not added.

| Configuration | Stage/tool | N | Wall s | wait4 tree CPU s | Sampled aggregate RSS MiB |
|---|---|---:|---:|---:|---:|
| colord-2w | stock Oximut | 5 | 2.075 (2.051–2.131) | 4.243 (4.177–4.339) | 441.395 (437.496–448.418) |
| colord-2w | ordinary Vitest before | 5 | 0.879 (0.877–0.910) | 1.139 (1.128–1.179) | 268.684 (264.129–275.949) |
| colord-2w | ordinary Vitest after | 5 | 0.886 (0.864–0.912) | 1.154 (1.126–1.171) | 272.445 (262.871–276.090) |
| colord-2w | Stryker before | 5 | 2.879 (2.857–2.934) | 5.155 (5.111–5.264) | 703.590 (700.879–706.074) |
| colord-2w | Stryker after | 5 | 2.878 (2.844–2.932) | 5.181 (5.134–5.259) | 705.363 (703.031–711.148) |
| colord-2w | full external preparation | 5 | 1.509 (1.404–1.687) | 1.929 (1.800–2.095) | 263.051 (259.828–274.789) |
| colord-2w | candidate prepared | 5 | 3.209 (3.100–3.234) | 5.552 (5.510–5.679) | 451.234 (437.988–494.859) |
| colord-2w | candidate first-use (unqualified) | 5 | 4.703 (4.613–4.787) | 7.518 (7.352–7.621) | 451.234 (437.988–494.859) |
| colord-4w | stock Oximut | 5 | 1.823 (1.800–1.846) | 4.565 (4.522–4.609) | 600.980 (559.402–611.320) |
| colord-4w | ordinary Vitest before | 5 | 0.878 (0.849–0.884) | 1.128 (1.105–1.136) | 265.043 (261.930–271.641) |
| colord-4w | ordinary Vitest after | 5 | 0.874 (0.852–0.887) | 1.129 (1.116–1.143) | 265.074 (258.297–269.844) |
| colord-4w | Stryker before | 5 | 3.051 (3.026–3.070) | 6.806 (6.782–6.885) | 993.918 (989.664–998.254) |
| colord-4w | Stryker after | 5 | 3.031 (2.987–3.087) | 6.840 (6.735–6.924) | 1001.977 (995.254–1017.266) |
| colord-4w | full external preparation | 5 | 1.578 (1.522–1.635) | 1.970 (1.953–2.010) | 272.992 (270.922–275.656) |
| colord-4w | candidate prepared | 5 | 2.882 (2.840–3.081) | 5.816 (5.717–6.026) | 640.055 (614.797–671.734) |
| colord-4w | candidate first-use (unqualified) | 5 | 4.517 (4.416–4.612) | 7.826 (7.670–7.983) | 640.055 (614.797–671.734) |
| colord-scale-2w | stock Oximut | 5 | 20.550 (19.915–20.823) | 40.843 (39.649–41.351) | 701.070 (686.051–705.191) |
| colord-scale-2w | ordinary Vitest before | 5 | 0.859 (0.829–0.867) | 1.104 (1.077–1.131) | 268.199 (263.816–272.406) |
| colord-scale-2w | ordinary Vitest after | 5 | 0.868 (0.861–0.895) | 1.112 (1.100–1.153) | 274.484 (265.047–276.477) |
| colord-scale-2w | Stryker before | 5 | 17.096 (16.824–17.618) | 41.666 (40.766–42.705) | 772.051 (763.508–787.770) |
| colord-scale-2w | Stryker after | 5 | 17.221 (17.099–17.726) | 42.040 (41.923–43.054) | 766.512 (745.457–775.188) |
| colord-scale-2w | full external preparation | 5 | 1.583 (1.509–1.628) | 1.978 (1.947–2.008) | 319.219 (271.504–326.559) |
| colord-scale-2w | candidate prepared | 5 | 24.740 (24.073–25.463) | 47.947 (46.616–48.825) | 719.641 (711.090–740.055) |
| colord-scale-2w | candidate first-use (unqualified) | 5 | 26.323 (25.701–26.972) | 49.926 (48.624–50.772) | 719.641 (711.090–740.055) |

The incomplete scale206/workers4 configuration has **no measured pair**. Its captured warmup resources, including the failed invocation, are:

| Warmup command/stage | Exit | Wall s | wait4 CPU s | Sampled RSS MiB |
|---|---:|---:|---:|---:|
| before/vitest | 0 | 0.852 | 1.106 | 270.766 |
| before/oximut | 1 | 16.750 | 45.885 | 906.750 |
| before/stryker | 0 | 15.354 | 49.881 | 1177.820 |
| after/vitest | 0 | 0.852 | 1.112 | 268.172 |
| after/oximut | 2 | 19.638 | 49.912 | 883.602 |
| full external preparation | 0 | 1.703 | 2.079 | 321.746 |
| failed candidate first-use total (not scored) | 2 | 21.341 | 51.990 | 883.602 |

## Real preparation, validation and sampling costs

Full setup is an external `node --experimental-import-meta-resolve .../integration.mjs NEW_OUTPUT` command, measured from process creation through exit/descendant reaping by `measurement.run_command`. Initial imports/checks, mkdir, complete inventory traversal/hashing, bundle/map/manifest writing and stdout/stderr are inside its observation. The narrow internal preparation diagnostic is not used as an end-to-end timing. Every invocation has a fresh owned artifact; none is amortized across CLI invocations.

Prepared execution retains all original/instrumented baselines, full validation once per actual controller (including every preexisting recycled controller), every worker artifact check, map registration/lazy consumption and all event/proc diagnostics. Full preparation stays separate in raw rows and is added per invocation for first-use. Dependency install, isolated release builds, fixture copying and post-command evidence checking remain outside every tool’s timing. No profiler was enabled.

Partial internal component diagnostics below include warmups. They are already inside measured execution; they are not complete isolated costs, and are never subtracted or added again.

| Configuration | Full controller validations | Worker artifact validations / recorded forks | Controller validation wall s/invocation, median(min–max) | Controller validation CPU s/invocation, median(min–max) | Worker validation wall s/invocation, median | Lazy map loads |
|---|---:|---:|---:|---:|---:|---:|
| colord-2w | 6 | 60 / 60 | 1.048 (1.023–1.140) | 1.188 (1.132–1.328) | 0.003 | 0 |
| colord-4w | 6 | 60 / 60 | 1.063 (0.957–1.182) | 1.182 (1.081–1.322) | 0.003 | 0 |
| colord-scale-2w | 18 | 1236 / 1236 | 3.285 (3.116–3.671) | 3.627 (3.436–4.088) | 0.065 | 0 |
| colord-scale-4w | 2 | 206 / 205 | 2.636 (2.636–2.636) | 2.813 (2.813–2.813) | 0.070 | 0 |

Every configured worker reads/hashes the bundle, map, registration metadata and helper. Worker-side map registration/stat costs and diagnostics remain in the total even when lazy map parsing is unused. Captured map events record zero lazy map loads; this does not make map or startup cost zero. No complete syscall I/O-byte or allocation profile was collected. Controller validation reads and hashes the full recorded installed/source inventory on every controller; all of its CPU/I/O remains counted. The historical manifest policy text “all costs unscored” was preserved as qualified source; the explicit outer command boundaries define the actual scored-attempt costs.

RSS is one harness sum of live `smaps_rollup` RSS for the process tree. Shared pages can count per process, the Python harness is excluded, and short-lived processes/peaks can be missed. Requested 20 ms cadence is not an achieved-cadence guarantee. CPU is kernel wait4 tree rusage, not a sum of sampled CPU counters. No Rust peak is added to the harness peak.

| Configuration | Observation group | Gap count | Median ms | p95 ms | p99 ms | Maximum ms |
|---|---|---:|---:|---:|---:|---:|
| colord-2w | after/oximut | 672 | 28.109 | 32.569 | 34.554 | 41.425 |
| colord-2w | after/stryker | 577 | 30.253 | 37.612 | 42.327 | 49.804 |
| colord-2w | after/vitest | 205 | 26.326 | 28.625 | 29.757 | 33.368 |
| colord-2w | before/oximut | 434 | 28.850 | 32.562 | 35.876 | 38.055 |
| colord-2w | before/stryker | 585 | 29.863 | 37.208 | 41.917 | 54.542 |
| colord-2w | before/vitest | 203 | 26.228 | 29.222 | 31.290 | 33.220 |
| colord-2w | setup | 345 | 26.945 | 29.248 | 31.939 | 37.148 |
| colord-4w | after/oximut | 602 | 27.292 | 42.601 | 51.375 | 59.069 |
| colord-4w | after/stryker | 546 | 29.370 | 58.100 | 66.704 | 74.681 |
| colord-4w | after/vitest | 205 | 25.892 | 28.086 | 29.848 | 32.309 |
| colord-4w | before/oximut | 354 | 29.291 | 45.080 | 51.255 | 54.596 |
| colord-4w | before/stryker | 555 | 28.987 | 59.393 | 68.944 | 75.224 |
| colord-4w | before/vitest | 205 | 25.480 | 27.457 | 28.536 | 34.946 |
| colord-4w | setup | 361 | 26.074 | 28.428 | 31.078 | 34.036 |
| colord-scale-2w | after/oximut | 5234 | 28.517 | 32.704 | 35.140 | 42.473 |
| colord-scale-2w | after/stryker | 3308 | 31.349 | 37.197 | 39.940 | 51.908 |
| colord-scale-2w | after/vitest | 210 | 25.130 | 26.954 | 27.580 | 27.917 |
| colord-scale-2w | before/oximut | 4344 | 28.540 | 31.493 | 33.199 | 38.464 |
| colord-scale-2w | before/stryker | 3305 | 31.145 | 37.128 | 40.490 | 50.937 |
| colord-scale-2w | before/vitest | 206 | 25.170 | 27.832 | 30.884 | 32.938 |
| colord-scale-2w | setup | 386 | 25.407 | 26.890 | 29.207 | 30.056 |
| colord-scale-4w | after/oximut | 594 | 30.647 | 47.612 | 51.690 | 56.572 |
| colord-scale-4w | after/vitest | 35 | 24.654 | 26.598 | 26.986 | 26.986 |
| colord-scale-4w | before/oximut | 497 | 31.951 | 46.050 | 49.469 | 52.547 |
| colord-scale-4w | before/stryker | 334 | 47.927 | 64.785 | 73.271 | 79.949 |
| colord-scale-4w | before/vitest | 34 | 24.943 | 27.686 | 28.583 | 28.583 |
| colord-scale-4w | setup | 68 | 25.112 | 26.411 | 27.545 | 27.545 |

Across 132 observed commands (warmups, measured commands and the failed invocation): 24404 actual gaps, median 29.087 ms, p95 37.875 ms, p99 51.465 ms, maximum 79.949 ms. All gaps remain in the compressed raw observations. These do not establish true peak RSS or any target bound.

## Exact recovery and provenance

Full artifact `14144152581`, digest `sha256:15c333d4c905139e5fb9ba558a80efdc319fb2840a349f5a3a497db9171425e9`; complete archive/log ZIP integrity checked, all 6789 native per-file hashes matched. Every regular candidate-source file (202) matches Git `88b7d6712648979dc7438a4044abf0f63650d463`; every stock-source file (51) matches stock `5dfd17e53cd7b067586b3ad47363cb3ecee5873e`. Both source archives include every Cargo workspace member. Rust/Cargo source and pins are byte-identical between the two revisions. Scored adapter/probe code is byte-identical to native-qualified `e9a368face7d8e3fd657383c2c979e2866370a38`; current measurement/scoring/validator code equals the archived source. All artifact/map/helper/inventory hashes for all 19 prepared artifacts were reconciled.

Both release binaries were built before timing with `--locked --release -p oximut-cli` in non-overlapping isolated Cargo targets. Stock SHA256 `fd76e97832fe5c39749b3f7f65ed797793cf54e78426b581756b168fd53f7230`; candidate SHA256 `5f931fa8f2559043579506864f280b40d55ec6d3ef9c9892d0a0acb65dd10a0e`. Complete binaries and build stdout/stderr/metadata are retained in the artifact.

Executed Node `v26.3.1`, Linux `x64`, path `/opt/hostedtoolcache/node/26.3.1/x64/bin/node`, binary SHA256 `0b52710a27133a3baf45f0d1f10689ab874f50e5821d8e611486d17e20bcacdd`. It matches the previously captured HTTPS upstream checksum → linux-x64 archive(`55647180e4ae58ffeaa3294e89aa4abda7c371dfbd64b44cbdb022980177aae0`) → extracted executable verification. This is the actual x64 native executable, not the local Android or ARM64 executable/archive hash. Vitest 5.1.0, Vite 8.3.5, Stryker core/runner 10.1.1, Rolldown 1.2.15, Babel 8.0.10 and magic-string 1.4.5 remain pinned. Native tools: npm 11.16.0, Rust/Cargo 1.96.0, Python 3.12.3. Exact machine, affinity/cgroup, lockfile, source and tool provenance is retained.

Recovery verified observed commands follow the exact prefix of the predeclared rotating serial order; timestamps agree, all observations fall within this run, all rows carry the new candidate SHA, full-setup argv/status and warmup labels agree, and the hook is present only for candidate Oximut. No worker1/thread/non-shared/cache mode was selected. Source/test inputs remain unchanged. Captured observations have no external timeout, memory error, truncated output or descendant-cleanup intervention. This validates recovery completeness for the **observed failed campaign**, not a complete144-row/24-artifact/2592-PID campaign or a correctness pass.

`artifact.zip` contains the original `w13-score-raw.tar.gz` plus native per-file inventory: source archives, both binaries, complete raw reports/output/statuses, every prepared artifact and compressed dependency/source inventories. `logs.zip` preserves workflow logs. API metadata, ZIP member hashes, compressed native file hashes and a raw observation export are alongside them. The executed `failure-recovery-source.py` produced `failure-recovery.json`; the retained success-only `verification-source.py` was **not executed or altered**. No false pass or scored verification was produced.

Owned temporary refs `perf/w13-score-native-1` and `perf/w13-score-native-2` were deleted only after complete raw/source recovery verification. Their exact source commits remain ancestors of the experiment branch; history is preserved. [Deletion receipt](native-43669808238/temporary-ref-cleanup.json).

## Decision and smallest next gate

Stop this candidate’s scoring. Do not infer wall<=0.50×, CPU<=0.80× or RSS<=0.80× Stryker, in either prepared or first-use mode. No scored bootstrap/chart is appropriate while the fourth correctness gate fails. No automatic retry, new experiment, default change, product integration, PR, merge or release follows this recovery.

The next justified work is a small non-scored investigation of the exact four-lane failure batch: preserve/expose the underlying native start/aggregate error and reconcile the single missing start/dispatch before any further qualification. The existing evidence is insufficient to blame the bundle, process-inspection check or native pool. Any future lifecycle/diagnostic change needs structural/product requalification, with its costs retained, before another predeclared scored campaign. The measured full setup commands cost roughly 1.4–1.8 s each and per-invocation controller validation is about 1.0 s for colord10 and 3.3 s for scale206/workers2; these are real recorded costs, not amounts to subtract to make a target pass.

Installation-bound output, six private allowlist modules and 15 native shared/API owners remain experimental. The prior collector-inlining no-go and all old failures stay preserved; this failure does not rule out every identity-safe bundling approach. Main remains unsettled.

[Raw recovery verification](native-43669808238/failure-recovery.json) · [Failure detail](native-43669808238/failure-detail.json) · [Raw observations](native-43669808238/raw-observations.json.gz) · [Complete artifact](native-43669808238/artifact.zip) · [Workflow logs](native-43669808238/logs.zip) · [Plan/history](PLAN.md)
