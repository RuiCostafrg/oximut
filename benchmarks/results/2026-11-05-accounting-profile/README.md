# Native accounting and peak RSS capture

[Run 42006490326](https://github.com/RuiCostafrg/oximut/actions/runs/42006490326), Ubuntu 24.04 x86_64. One overhead-bearing diagnostic, no warmup or paired campaign. Exact 41-source/102-test/five-file/ten-killed-edit qualification and counter/snapshot collection pass. Source head `aa9890c`; native test merge `3b3af02a5196c6ba9a3dd7784f4400cc3eb34ec7`.

[Canonical HTML report](../performance.html). Native Node/Python sampling was disabled for this capture. On GitHub, choose **Raw**, save the HTML file, then open the saved file in a browser; a local checkout can open `benchmarks/results/performance.html` directly.

| Tool | Wall s | CPU s (user/sys) | Aggregate RSS bytes | Peak/observed processes |
|---|---:|---:|---:|---:|
| vitest | 1.325232 | 1.732233 (1.426343/0.305890) | 281894912 | 2/5 |
| oximut | 5.326470 | 7.757920 (5.250769/2.507151) | 348655616 | 4/21 |
| stryker | 4.736807 | 6.271099 (5.591381/0.679718) | 566251520 | 3/3 |

**Paired ratios and bootstrap CIs: N/A.** This profiled run cannot establish speedup or goal achievement.

## Sampling windows

| Nested stage | Calls | Wall ms | Raw self user ms | Raw self sys ms |
|---|---:|---:|---:|---:|
| sampling | 106 | 1303.983687 | 21.183000 | 1236.656000 |
| discovery | 106 | 76.701333 | 10.606000 | 38.940000 |
| identityReads | 787 | 15.782672 | 3.774000 | 73.963000 |
| rssReads | 784 | 1196.798264 | 6.383000 | 1121.165000 |

RSS-read windows dominate observed sampling wall time. These windows include timer overhead and scheduling effects; eliminating their full wall interval is not an established saving.

**Clock-boundary limitation:** identityReads reports user+sys greater than its wall interval. Collection completeness is not a precision guarantee. Do not interpret the fine-grained getrusage splits as exact stage CPU attribution or subtract them to label a remainder as worker CPU. Review the unmatched interval endpoints and remeasure total thread CPU with CLOCK_THREAD_CPUTIME_ID. The raw original counters remain unchanged; no causal explanation is assumed.

CPU counters cover only CLI sampling windows, excluding coordinator fingerprints/cleanup, other CLI work and workers. Stage totals nest inside sampling; no exact process-tree partition is claimed.

## resources.phaseWallMs

| Phase | ms |
|---|---:|
| finalInputValidation | 0.000241 |
| inputAndSchedulerPreparation | 3.479556 |
| instrumentedBaseline | 995.951736 |
| mutationExecution | 2849.080946 |
| mutationPlanning | 0.084989 |
| originalBaseline | 1406.364968 |
| postBaselineValidation | 0.000160 |
| teardown | 61.715181 |

## Same peak sample, per-process RSS

These are components of each aggregate peak, not independent per-process maxima. Shared pages count per process. Cross-PID sampling is not atomic. No command-line arguments or environment values were collected.

| Tool | PID | Parent | Name | RSS bytes |
|---|---:|---:|---|---:|
| vitest | 6510 | 6473 | node-MainThread | 198492160 |
| vitest | 6537 | 6510 | node-MainThread | 83402752 |
| oximut | 6558 | 6473 | oximut | 5218304 |
| oximut | 6559 | 6558 | oximut | 4927488 |
| oximut | 6560 | 6559 | node-MainThread | 249303040 |
| oximut | 6690 | 6560 | node-MainThread | 89206784 |
| stryker | 6715 | 6473 | stryker | 183791616 |
| stryker | 6730 | 6715 | node-MainThread | 122523648 |
| stryker | 6745 | 6715 | node-MainThread | 259936256 |

Original diagnostic, qualification summary/rows/metadata, full Oximut report and build metadata are retained with input hashes. Binary, source archive, prepared projects and logs remain only in workflow artifacts with 14-day retention; hashes cannot reconstruct omitted files after expiry. The canonical report revalidates retained qualification and capture evidence before rendering.

For a scored comparison, use [the separate five-pair native control](../2026-11-05-accounting-control/README.md). Only RSS currently meets the mission target; no release is claimed.
