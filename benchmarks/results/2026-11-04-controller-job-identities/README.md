# Native capture reconciled against qualified job and run identities

[Run 41930938613](https://github.com/RuiCostafrg/oximut/actions/runs/41930938613) captured head `24f21ae` via merge revision `cccafbd502aa533b4b66627bf8bff0a356837797` on Ubuntu 24.04 x86_64. Both the validator and controller preload match the native build's source hashes.

Exact workload qualification and current capture validation pass: 41 sources, 102 tests, five mutation files and ten identical killed arithmetic edits. The capture contains one controller, 14 unique serial commands (initialize, two baselines, ten mutants, close), 18 observed fork lifecycles and one finalized first-mutant CPU profile. Every captured mutant digest matches one unique executed mutant in the retained qualification report. The baseline job IDs are `baseline` and `instrumented-baseline`; every execution job belongs to the report run ID, and each mutant job ID matches its mutation. Counts also reconcile. Event sequences are contiguous, write-failure flags are clear and all reply observations follow normal local send returns. Earlier captures remain unchanged as historical raw evidence and fail the current identity/integrity gates.

## Sampling scope and job observations

The first mutant's request-observation-to-local-send-return interval is **425.404 ms**. Profiler activation acknowledgment arrives **80.518 ms** after request observation; acknowledgment-to-send-return is **344.886 ms**. These boundaries observe neither recipient delivery nor callback completion. CPU sampling begins during profiler activation, omits some request prefix and may include inspector overhead; acknowledgment latency is not an exact unsampled duration. The CPU profile describes the controller alone, includes idle frames and excludes fork CPU. It is not a full-request or process-tree CPU partition.

Independent mutant boundary medians (not additive; raw per-job intervals, controller command IDs, job IDs, run IDs and mutant digests are in `boundary-intervals.json`):

| Interval | Median ms |
|---|---:|
| Request observation to local send return | 251.582 |
| Request observation to fork | 5.498 |
| Fork to started response | 114.632 |
| Run dispatch observation to collection RPC | 40.351 |
| Collection to testfileFinished | 81.894 |
| testfileFinished to local controller send return | 6.970 |

## Resources (one profiled run, overhead-bearing)

| Tool | Wall s | CPU s (user / sys) | Peak aggregate RSS bytes | Peak / observed processes |
|---|---:|---:|---:|---:|
| vitest | 1.376447 | 1.786830 (1.452751 / 0.334079) | 285978624 | 2 / 5 |
| oximut | 5.592469 | 8.260947 (5.555036 / 2.705911) | 368017408 | 4 / 21 |
| stryker | 5.439191 | 6.871380 (6.143770 / 0.727610) | 563277824 | 3 / 3 |

Paired ratios and bootstrap 95% CIs are **not available**: this diagnostic has one run, no warmup and profiler overhead. It cannot establish product gains or target achievement. The [qualified same-engine control](../2026-11-04-native-control/README.md) retains repeated measurements and paired CIs; its after/before intervals include 1. These profiled timings must not be subtracted from other hosts/campaigns to infer savings.

`resources.phaseWallMs`:

| Phase | Wall ms |
|---|---:|
| finalInputValidation | 0.000171 |
| inputAndSchedulerPreparation | 3.766333 |
| instrumentedBaseline | 1027.970533 |
| mutationExecution | 3058.678794 |
| mutationPlanning | 0.081052 |
| originalBaseline | 1428.325916 |
| postBaselineValidation | 0.000160 |
| teardown | 62.792730 |

## Retained evidence and revalidation

This capsule retains unmodified native timeline/profile files, diagnostic summary, qualification summary/measurement rows, full Oximut report and build metadata. Derived revalidation and boundary intervals are identified separately. `SHA256SUMS` covers every retained file except itself. The source archive, prepared projects, binary, other tools' reports and logs remain only in the workflow artifact with configured 14-day retention; committed hashes cannot reconstruct omitted files after expiry or rerun the workload without them.

From the repo root, rerun the stored evidence checks:

```sh
PYTHONPATH=benchmarks python3 - <<'PYTHON'
from pathlib import Path
from profile_native import controller_capture_inventory, qualification_errors
root = Path('benchmarks/results/2026-11-04-controller-job-identities')
assert not qualification_errors(root / 'qualification', 0)
capture = controller_capture_inventory(root / 'profiles', root / 'qualification')
assert not capture['errors'] and capture['workload_reconciled']
PYTHON
```

This revalidates retained evidence rather than executing a new benchmark. No product execution code or correctness/isolation/cleanup gate changed. The next experiment remains an opt-in warm runtime with a proven fresh realm and dependency graph, as ranked in [the workstream plan](../../../docs/edgy-performance.md).
