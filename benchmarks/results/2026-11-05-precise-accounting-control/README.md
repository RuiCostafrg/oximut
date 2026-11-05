# Precise-clock change: native paired control

[Run 42058759907](https://github.com/RuiCostafrg/oximut/actions/runs/42058759907), Ubuntu 24.04 x86_64. One warmup plus five measured pairs. Exact 41-source/102-test/five-file/ten-killed-edit workload and strict build integrity pass. Profiling clocks and component recording were disabled. This is a measurement change, not an intended engine optimization.

Before `873c85ff46064f582e057cc82d90b45f29821ec5`; reviewed after head `9aeb4a0bf7763fe895bc2c9bb51946fb78f7ff17`; native test merge `385c2d534fdb06faf55572bb3158f6e1eb7afb2f`.

[Canonical HTML](../performance.html). On GitHub choose Raw, save the HTML and open it in a browser. Rebuild with `python3 benchmarks/performance_report.py --output /tmp/oximut-performance.html`.

Independent medians; median user + median sys need not equal median total CPU. Decimal MB.

| Tool | Wall s | CPU s (user/sys) | Aggregate RSS MB | Peak/observed processes |
|---|---|---|---|---|
| after/oximut | 5.354715 | 7.686855 (5.310565/2.390588) | 331.538 | 4/21 |
| after/stryker | 4.730195 | 6.272786 (5.617704/0.655082) | 575.255 | 3/3 |
| after/vitest | 1.352397 | 1.694122 (1.412196/0.294144) | 277.537 | 2/5 |
| before/oximut | 5.337462 | 7.711324 (5.269475/2.435951) | 347.066 | 4/21 |
| before/stryker | 4.704383 | 6.230139 (5.612190/0.628776) | 569.520 | 3/3 |
| before/vitest | 1.328917 | 1.709359 (1.413688/0.298525) | 279.212 | 2/5 |

| Paired comparison | Wall [95% CI] | CPU [95% CI] | RSS [95% CI] |
|---|---|---|---|
| after_vs_before | 1.000037 [0.991318, 1.016845] | 0.992331 [0.986948, 1.038172] | 0.955259 [0.913886, 0.971762] |
| after_vs_stryker | 1.132685 [1.034099, 1.144781] | 1.228145 [1.178525, 1.262246] | 0.576727 [0.565256, 0.587459] |
| before_vs_stryker | 1.135154 [1.125811, 1.151412] | 1.233663 [1.228127, 1.265457] | 0.606611 [0.601037, 0.631004] |

Mission CI upper bounds: wall 0.50, CPU 0.80, RSS 0.80. Wall/CPU miss; RSS passes. Wall and CPU after/before intervals include 1. The RSS control interval is below 1, but its cause is not established by this instrumentation-only change; no causal speedup claim or release follows. These five-pair intervals remain exploratory and host/workload specific.

## resources.phaseWallMs

| Phase | After ms | Before ms |
|---|---|---|
| finalInputValidation | 0.000221 | 0.000150 |
| inputAndSchedulerPreparation | 3.583923 | 3.604721 |
| instrumentedBaseline | 991.618861 | 1016.988401 |
| mutationExecution | 2867.886050 | 2838.664953 |
| mutationPlanning | 0.083637 | 0.078836 |
| originalBaseline | 1438.140783 | 1418.868693 |
| postBaselineValidation | 0.000260 | 0.000160 |
| teardown | 49.650948 | 49.451954 |

Phase medians are independent and not additive. Unmodified summary, raw rows and build metadata are retained with input hashes. Full prepared projects, source archives, binaries and logs remain workflow-only (14-day retention). No cross-campaign subtraction is used.
