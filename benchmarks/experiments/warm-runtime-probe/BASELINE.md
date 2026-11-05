# Post-#38 native control baseline

[Run 41934358575](https://github.com/RuiCostafrg/oximut/actions/runs/41934358575). Ubuntu 24.04 x86_64; one warmup and five measured pairs. Exact 41-source/102-test/five-mutated-file/ten-killed-edit correctness and strict build-integrity gates pass. This is the diagnostic-only change before merge; no engine speedup is attributable to it. Full original artifacts are downloaded under /tmp/oximut-41934358575-bT7KgJ; workflow-only retention is 14 days.

Independently computed medians, after candidate (fresh forks):

| Tool | Wall s | CPU s (user/sys) | Peak aggregate RSS bytes | Peak/observed processes |
|---|---:|---:|---:|---:|
| oximut | 5.287401 | 7.781574 (5.152017/2.630735) | 338694144 | 4/21 |
| stryker | 4.615678 | 6.134028 (5.449621/0.705794) | 571056128 | 3/3 |
| vitest | 1.292046 | 1.660245 (1.337674/0.313721) | 275660800 | 2/5 |

Paired median ratios [bootstrap 95% CI]:

| Comparison | Wall | CPU | RSS |
|---|---:|---:|---:|
| after_vs_stryker | 1.153741 [1.120200, 1.160273] | 1.266485 [1.248683, 1.277047] | 0.590088 [0.579662, 0.607547] |
| after_vs_before | 1.005656 [0.988565, 1.063388] | 1.011649 [0.978667, 1.029246] | 0.997052 [0.969250, 1.025863] |

Mission target upper bounds: wall 0.50, CPU 0.80, RSS 0.80. Wall and CPU miss; only RSS passes. Legacy harness thresholds differ; no goal or release claim.

Median `resources.phaseWallMs` (after, measured):

| Phase | ms |
|---|---:|
| finalInputValidation | 0.000180 |
| inputAndSchedulerPreparation | 4.055486 |
| instrumentedBaseline | 1011.011421 |
| mutationExecution | 2770.858073 |
| mutationPlanning | 0.086380 |
| originalBaseline | 1414.294830 |
| postBaselineValidation | 0.000320 |
| teardown | 63.110447 |

Source attribution metadata: before 74a4972034d9ad7a31566d693d3762ac42c187fc, after 53ec1de765cd9a5c6cab9fd0449ec3d64950928e. No cross-campaign subtraction or local timing is used for a performance claim.
