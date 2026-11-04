# Historical guarded capture: pre-send observations, partial CPU sampling

[Run 41852828217](https://github.com/RuiCostafrg/oximut/actions/runs/41852828217) captured PR head `96ccf5f` via merge revision `335e15de776e4a5ecec9840eb018d3b52c2eb561` on Ubuntu 24.04 x86_64.

Exact workload qualification passes: 41 sources, 102 tests, five mutation files, ten identical killed edits. The original capture gate passed with one controller, 14 unique serial commands, 18 observed fork lifecycles and a finalized first-mutant CPU profile. The explicit synchronous-inspector guard succeeds natively. **Current capture revalidation fails:** this historical unversioned trace ends reply observations before the send call and lacks sequence/write-failure attestation. Its original successful summary is historical, not proof of completeness under the new gate. A fresh capture is required. This establishes no product speed or target achievement.

This directory durably retains the unmodified diagnostic summary, CPU profile, timeline, measurement rows, workload summary and build metadata, plus extracted Oximut resources and current-validator revalidation. The full source archive, prepared projects, binary and logs remain only in the workflow artifact, with configured 14-day retention; hashes cannot recover omitted files after expiry. `SHA256SUMS` covers the committed files.

## Sampling scope

The observed request-to-pre-send interval for the first mutant is **363.671 ms**. This ends before `process.send` is attempted and establishes neither its return nor recipient delivery. The profiler activation acknowledgment arrives **79.603 ms** after the request; acknowledgment-to-pre-send is **284.067 ms**. CPU sampling starts during profiler activation, omits some request prefix, and may include inspector startup/stop overhead. The activation latency is not an exact unsampled duration. The CPU file describes the controller alone, excludes test-fork CPU, includes idle frames, and is not a full-request or whole-process-tree CPU partition.

## Raw resource metrics (profiler overhead; one run)

| Tool | Wall s | CPU s (user / sys) | Aggregate peak RSS bytes | Peak / observed processes |
|---|---:|---:|---:|---:|
| vitest | 1.145133 | 1.519139 (1.236434 / 0.282705) | 282910720 | 2 / 5 |
| oximut | 5.145387 | 7.346287 (4.877426 / 2.468861) | 332156928 | 4 / 21 |
| stryker | 4.236203 | 5.617345 (4.956631 / 0.660714) | 575205376 | 4 / 4 |

No paired ratios or bootstrap CIs are published for this one-shot profiled capture. These measurements cannot establish performance gains. The [qualified same-engine control](../2026-11-04-native-control/README.md) retains the repeated campaign and its paired CIs.

`resources.phaseWallMs` (ms):

| Phase | Wall ms |
|---|---:|
| finalInputValidation | 0.000200 |
| inputAndSchedulerPreparation | 3.657121 |
| instrumentedBaseline | 932.607478 |
| mutationExecution | 2743.565884 |
| mutationPlanning | 0.074541 |
| originalBaseline | 1397.084199 |
| postBaselineValidation | 0.000171 |
| teardown | 60.975807 |

The original raw artifacts from the [earlier capture](../2026-11-04-whole-mutant-profile/README.md) remain unchanged. This follow-up verifies the added startup guard; it does not make earlier CPU sampling cover the omitted request prefix.
