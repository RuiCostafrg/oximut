# Accounting-change native paired control

[Run 42006440592](https://github.com/RuiCostafrg/oximut/actions/runs/42006440592), Ubuntu 24.04 x86_64, one warmup plus five pairs. Exact workload (41 sources, 102 tests, five mutation files, ten killed edits) and strict build integrity pass. Diagnostic clocks and component recording were disabled. This PR adds measurement capability, not an optimization.

[Canonical HTML report](../performance.html) combines this control with the separately profiled diagnostic and historical controller traces. Rebuild with `python3 benchmarks/performance_report.py --output /tmp/oximut-performance.html`. On GitHub, choose **Raw**, save the HTML file, then open the saved file in a browser; a local checkout can open `benchmarks/results/performance.html` directly.

Before `873c85ff46064f582e057cc82d90b45f29821ec5`; after native test merge `3b3af02a5196c6ba9a3dd7784f4400cc3eb34ec7`, reviewed head `aa9890c497b95f45e2ec91aacc61c9e9c555dc9a`.

Independent medians; user/sys medians need not sum to median CPU total. Decimal MB.

| Side/tool | Wall s | CPU s (user/sys) | Aggregate RSS MB | Peak/observed processes |
|---|---:|---:|---:|---:|
| after/oximut | 5.332525 | 7.754821 (5.333086/2.433625) | 338.989 | 4/21 |
| after/stryker | 4.819497 | 6.401057 (5.679574/0.661948) | 571.023 | 3/3 |
| after/vitest | 1.340435 | 1.733198 (1.426227/0.299480) | 277.852 | 2/5 |
| before/oximut | 5.526761 | 7.996567 (5.406856/2.589711) | 342.131 | 4/21 |
| before/stryker | 4.790712 | 6.345796 (5.652424/0.693363) | 575.304 | 3/3 |
| before/vitest | 1.344461 | 1.723076 (1.424852/0.298224) | 281.764 | 2/5 |

| Paired comparison | Wall [95% CI] | CPU [95% CI] | RSS [95% CI] |
|---|---:|---:|---:|
| after_vs_stryker | 1.139229 [1.098870, 1.159126] | 1.243261 [1.209529, 1.279208] | 0.598910 [0.561770, 0.600502] |
| before_vs_stryker | 1.153351 [1.119228, 1.171559] | 1.260998 [1.232977, 1.274908] | 0.594696 [0.583776, 0.599532] |
| after_vs_before | 0.993555 [0.958246, 1.002785] | 0.990806 [0.968198, 1.011504] | 0.999198 [0.941816, 1.006384] |

Mission CI upper bounds: wall 0.50, CPU 0.80, RSS 0.80. Wall and CPU miss; RSS passes. All after/before CIs include 1; no attributable speedup is established. Legacy evaluator thresholds differ and are not used for a mission success claim.

`resources.phaseWallMs` independent medians (not additive):

| Phase | After ms | Before ms |
|---|---:|---:|
| finalInputValidation | 0.000240 | 0.000140 |
| inputAndSchedulerPreparation | 3.624693 | 3.559316 |
| instrumentedBaseline | 1006.083171 | 1031.519244 |
| mutationExecution | 2857.572219 | 2975.373759 |
| mutationPlanning | 0.077394 | 0.076732 |
| originalBaseline | 1459.360348 | 1447.018006 |
| postBaselineValidation | 0.000160 | 0.000150 |
| teardown | 61.144358 | 49.531730 |

Unmodified summary, raw resource rows and build metadata are retained with SHA-256 input hashes. Full projects, binary, source archives and logs remain only in workflow artifacts (14-day retention); hashes cannot reconstruct omitted files. The HTML derives its values from these raw inputs and validates paired-ratio reconciliation.
