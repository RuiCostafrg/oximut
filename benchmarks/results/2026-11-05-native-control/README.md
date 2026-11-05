# Native control and hotspot report

[Open the offline HTML report](report.html). Rebuild with `python3 benchmarks/performance_report.py --output /tmp/oximut-performance.html`.

[Benchmark 41973869522](https://github.com/RuiCostafrg/oximut/actions/runs/41973869522), Ubuntu 24.04 x86_64, one warmup and five measured pairs. Exact 41-source/102-test/five-file/ten-killed-edit workload and strict build integrity pass. This is the experimental-only PR #39 control, not an engine optimization.

Before: `9dc25e00819fa278332d6c16cc9b81d35010a90b`; after CI test merge: `df4a6a9d9d3e5ef5ee1968567ec5d8a3abe4ea44`; reviewed PR head: `2b4c20fe2dbafa739196561e533f23b54053f97f`; merged main: `873c85ff46064f582e057cc82d90b45f29821ec5`.

Raw independent medians (CPU components need not sum after independently taking medians; decimal MB):

| Side | Tool | Wall s | CPU s (user/sys) | Aggregate RSS MB | Peak/observed processes |
|---|---|---:|---:|---:|---:|
| after | oximut | 4.718770 | 6.186250 (4.122216/2.110701) | 346.395 | 4/21 |
| after | stryker | 3.722032 | 4.935994 (4.382217/0.571588) | 569.315 | 3/3 |
| after | vitest | 1.086928 | 1.374647 (1.109276/0.266170) | 275.862 | 2/5 |
| before | oximut | 4.838870 | 6.246341 (4.145844/2.100497) | 335.151 | 4/21 |
| before | stryker | 3.830692 | 4.987091 (4.407305/0.579786) | 571.970 | 3/3 |
| before | vitest | 1.086351 | 1.402724 (1.132969/0.268153) | 279.196 | 2/5 |

Paired median ratios [bootstrap 95% CI]:

| Comparison | Wall | CPU | RSS |
|---|---:|---:|---:|
| after_vs_stryker | 1.267794 [1.215841, 1.385619] | 1.260988 [1.242875, 1.301697] | 0.608441 [0.578680, 0.616663] |
| before_vs_stryker | 1.247411 [1.186360, 1.537949] | 1.246019 [1.237138, 1.262898] | 0.582966 [0.563917, 0.600664] |
| after_vs_before | 0.973151 [0.840537, 1.198038] | 1.028947 [0.980309, 1.047030] | 1.029805 [0.976437, 1.092030] |

Mission CI upper bounds are wall ≤0.50, CPU ≤0.80, RSS ≤0.80. Wall and CPU miss; only RSS passes. The original evaluator still uses older wall/RSS thresholds, so its target flags are not the mission gate. Every after/before CI includes 1; no attributable gain or release claim.

`resources.phaseWallMs`, independent measured medians (not additive):

| Phase | After ms | Before ms |
|---|---:|---:|
| finalInputValidation | 0.000180 | 0.000180 |
| inputAndSchedulerPreparation | 3.135446 | 3.255844 |
| instrumentedBaseline | 797.990406 | 832.797048 |
| mutationExecution | 2787.812032 | 2582.086279 |
| mutationPlanning | 0.068351 | 0.068291 |
| originalBaseline | 1136.422568 | 1137.583675 |
| postBaselineValidation | 0.000210 | 0.000190 |
| teardown | 48.858403 | 59.526968 |

The separate [native structural oracle 41973869479](https://github.com/RuiCostafrg/oximut/actions/runs/41973869479) matches the executed source hashes and validates cleanup. Its raw warm-host counterexample still leaks host state. The narrow recycling guard passes only its tested scope; no product warm-reuse mode is enabled. Structural wall/CPU/RSS/process metrics, phases, ratios and CIs are unmeasured/N/A.

The HTML separately revalidates retained [controller capture 41930938613](../2026-11-04-controller-job-identities/README.md), shows its overhead-bearing resources, phase timings and partial V8 sample weights, and labels unmeasured worker CPU. It must not be subtracted from this campaign.

Original summary/resource rows/build metadata and native structural result records are retained unchanged with SHA-256 checksums. Prepared projects, source archives, binary, logs and full validation artifacts remain only in workflow artifacts with 14-day retention. Hashes cannot reconstruct omitted files after expiry. The generated HTML is derived output and intentionally excluded from the input checksum manifest.

Native Rust accounting/per-process peak RSS attribution is pending. Local Android/PRoot runs establish only structural correctness. Regenerate the same page with `--accounting /path/to/qualified/capture` after the new diagnostic passes.
