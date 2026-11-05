# Native precise thread CPU and peak RSS capture

[Run 42058991084](https://github.com/RuiCostafrg/oximut/actions/runs/42058991084), Ubuntu 24.04 x86_64. One overhead-bearing diagnostic, no warmup or paired campaign. Exact 41-source/102-test/five-file/ten-killed-edit qualification and version 2 counter/snapshot collection pass. Reviewed head `9aeb4a0bf7763fe895bc2c9bb51946fb78f7ff17`; native test merge `385c2d534fdb06faf55572bb3158f6e1eb7afb2f`.

[Canonical HTML](../performance.html). On GitHub choose Raw, save the HTML and open it in a browser. Node/Python profilers were disabled for this capture.

| Tool | Wall s | CPU s (user/sys) | Aggregate RSS MB | Peak/observed processes |
|---|---|---|---|---|
| vitest | 0.998246 | 1.285986 (1.033965/0.252021) | 275.657 | 2/5 |
| oximut | 3.934345 | 5.471774 (3.780658/1.691116) | 320.160 | 4/21 |
| stryker | 3.543291 | 4.684165 (4.115617/0.568548) | 578.470 | 3/3 |

**Paired ratios and bootstrap CIs: N/A.** This diagnostic does not establish speedup. Its wall/CPU values cannot be subtracted from another campaign.

## Sampling windows

| Nested stage | Calls | Wall ms | Precise thread CPU ms | Raw user ms | Raw sys ms |
|---|---|---|---|---|---|
| sampling | 78 | 718.282268 | 704.948055 | 19.012000 | 687.345000 |
| discovery | 78 | 55.274234 | 55.052584 | 9.996000 | 45.194000 |
| identityReads | 580 | 10.743085 | 9.483882 | 0.441000 | 11.119000 |
| rssReads | 574 | 643.797507 | 629.542601 | 8.155000 | 622.571000 |

All CPU/timing failure counters are zero. Precise CPU endpoints lie inside wall endpoints. Stage windows nest inside sampling and include instrumentation overhead; their full duration is not an established recoverable wall saving. Raw getrusage splits remain unsuitable for exact attribution even after endpoint alignment.

| Same CLI thread interval | Precise CPU ms |
|---|---|
| Budget construction through resource snapshot | 744.762242 |
| Sampling windows | 704.948055 |
| Outside sampling within Budget interval | 39.814187 |

Budget totals exclude earlier startup, other processes and gaps between retry intervals. The remainder is unwindowed CLI work, not worker CPU; no full process-tree CPU partition is claimed.

## resources.phaseWallMs

| Phase | ms |
|---|---|
| finalInputValidation | 0.000251 |
| inputAndSchedulerPreparation | 2.912413 |
| instrumentedBaseline | 749.638257 |
| mutationExecution | 2066.079117 |
| mutationPlanning | 0.063786 |
| originalBaseline | 1064.524479 |
| postBaselineValidation | 0.000170 |
| teardown | 44.754223 |

## Components of the same aggregate RSS peak

Shared pages count per process; snapshots across PIDs are not atomic. These are not independent per-process maxima.

| Tool | PID | Parent | Name | Start ticks | RSS bytes |
|---|---|---|---|---|---|
| vitest | 6351 | 6314 | node-MainThread | 17634 | 199200768 |
| vitest | 6392 | 6351 | node-MainThread | 17717 | 76455936 |
| oximut | 6399 | 6314 | oximut | 17736 | 5312512 |
| oximut | 6400 | 6399 | oximut | 17736 | 5132288 |
| oximut | 6401 | 6400 | node-MainThread | 17737 | 220119040 |
| oximut | 6516 | 6401 | node-MainThread | 18049 | 89595904 |
| stryker | 6555 | 6314 | stryker | 18143 | 192921600 |
| stryker | 6571 | 6555 | node-MainThread | 18220 | 123711488 |
| stryker | 6586 | 6555 | node-MainThread | 18258 | 261836800 |

Original diagnostic, qualified reports/rows/metadata and build metadata are retained with SHA-256 hashes. Binaries, prepared projects, source archive and logs remain workflow-only with 14-day retention; hashes cannot reconstruct omitted files after expiry. The separate [paired control](../2026-11-05-precise-accounting-control/README.md) is the scored evidence.
