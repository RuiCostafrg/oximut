# Rejected exit-time Node CPU capture

[Run 42058984932](https://github.com/RuiCostafrg/oximut/actions/runs/42058984932), native Ubuntu 24.04 x86_64, head `9aeb4a0bf7763fe895bc2c9bb51946fb78f7ff17` (workflow dispatch, not the PR test merge).

**Profile capture FAILED.** The exact 41-source/102-test/five-file/ten-killed-edit qualification passed, but Vitest workers did not finalize their exit-time identities/profiles. In particular the required `oximut_test_fork` role has no finalized capture. No worker CPU attribution or complete worker profile is claimed. Existing validation rejected this evidence rather than filling gaps or weakening cleanup. The lifecycle/termination mechanism must be audited before choosing a diagnostic flush boundary.

This is one overhead-bearing run. Paired ratios and bootstrap CIs: **N/A**. The following resources describe the profiled workload only, not a speedup or a comparison with other campaigns.

| Tool | Wall s | CPU s (user/sys) | Aggregate RSS MB | Peak/observed processes |
|---|---|---|---|---|
| vitest | 1.352090 | 1.817248 (1.539540/0.277708) | 303.473 | 2/5 |
| oximut | 5.160446 | 7.167248 (5.668801/1.498447) | 363.287 | 4/21 |
| stryker | 4.974277 | 6.975743 (6.261688/0.714055) | 620.065 | 4/4 |

## resources.phaseWallMs

| Phase | ms |
|---|---|
| finalInputValidation | 0.000381 |
| inputAndSchedulerPreparation | 2.069070 |
| instrumentedBaseline | 983.017207 |
| mutationExecution | 2726.514075 |
| mutationPlanning | 0.078375 |
| originalBaseline | 1391.691752 |
| postBaselineValidation | 0.000230 |
| teardown | 51.560417 |

Diagnostic status, workload qualification data, original build metadata and all 44 identity JSON records are retained with hashes. The 20 CPU profile files are **not retained here**; their sizes/digests are in `omitted-profile-files.json`, and the complete workflow artifact has 14-day retention. Digests cannot reconstruct omitted files after expiry. Even the existing finalized profiles do not make the missing worker role complete.
