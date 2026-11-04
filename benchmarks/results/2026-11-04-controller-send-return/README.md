# Native capture with local send-return observations

[Run 41890546678](https://github.com/RuiCostafrg/oximut/actions/runs/41890546678) captured head `fb47d6c` via merge revision `d25b431704c9b6667d9b1d4bc253164223b24c4f` on Ubuntu 24.04 x86_64. Both the validator and controller preload match the native build's source hashes.

Exact workload qualification and current capture validation pass: 41 sources, 102 tests, five mutation files, ten identical killed arithmetic edits; one controller, 14 unique serial commands (initialize, two baselines, ten mutants, close), and 18 observed fork lifecycles. Workload counts reconcile against the retained Oximut report. The version-1 timeline has contiguous event sequences, no append-failure marker/flag, and normal local send-return observations. Historical traces in the earlier capsules remain unchanged and fail this stronger gate.

## Timing and sampling scope

The first mutant's request-observation-to-local-send-return interval is **500.787 ms**. Profiler activation acknowledgment is **60.340 ms** after request observation; acknowledgment-to-send-return is **440.447 ms**. These boundaries do not observe recipient delivery or callback completion. CPU sampling begins during profiler activation, omits some request prefix and may include inspector overhead; acknowledgment latency is not an exact unsampled duration. The CPU profile covers only the controller, includes idle frames and excludes fork CPU. It is not a full-request or process-tree CPU partition.

Independent mutant boundary medians (not additive; raw per-job intervals are retained in `boundary-intervals.json`):

| Interval | Median ms |
|---|---:|
| Request observation to local send return | 147.673 |
| Request observation to fork | 4.233 |
| Fork to started response | 70.402 |
| Run dispatch observation to collection RPC | 22.242 |
| Collection to testfileFinished | 42.396 |
| testfileFinished to local controller send return | 5.186 |

## Resources (one profiled run, overhead-bearing)

| Tool | Wall s | CPU s (user / sys) | Peak aggregate RSS bytes | Peak / observed processes |
|---|---:|---:|---:|---:|
| vitest | 0.880369 | 1.123482 (0.906155 / 0.217327) | 283070464 | 2 / 5 |
| oximut | 4.156049 | 5.017400 (3.297288 / 1.720112) | 357167104 | 5 / 21 |
| stryker | 2.813492 | 3.810705 (3.322920 / 0.487785) | 573894656 | 3 / 3 |

Paired ratios and bootstrap 95% CIs are **not available**: this diagnostic has one run, no warmup and profiling overhead. Do not use it to claim product gains or target achievement. The [qualified same-engine control](../2026-11-04-native-control/README.md) retains repeated measurements and paired CIs; its after/before intervals include 1.

`resources.phaseWallMs`:

| Phase | Wall ms |
|---|---:|
| finalInputValidation | 0.000361 |
| inputAndSchedulerPreparation | 2.529129 |
| instrumentedBaseline | 617.715143 |
| mutationExecution | 2610.208283 |
| mutationPlanning | 0.055773 |
| originalBaseline | 878.051006 |
| postBaselineValidation | 0.000200 |
| teardown | 42.859484 |

## Retained evidence and revalidation

This capsule retains unmodified native timeline/profile files, diagnostic summary, qualification summary/measurement rows, full Oximut report and build metadata. Derived revalidation and boundary intervals are identified separately. `SHA256SUMS` covers every retained file except itself. The source archive, prepared projects, binary, other tools' reports and logs remain only in the workflow artifact with configured 14-day retention; committed hashes cannot reconstruct omitted files after expiry or rerun the workload without them.

From the repo root, rerun the retained evidence checks:

```sh
PYTHONPATH=benchmarks python3 - <<'PYTHON'
from pathlib import Path
from profile_native import controller_capture_inventory, qualification_errors
root = Path('benchmarks/results/2026-11-04-controller-send-return')
assert not qualification_errors(root / 'qualification', 0)
assert not controller_capture_inventory(root / 'profiles', root / 'qualification')['errors']
PYTHON
```

This revalidates stored evidence, not a new benchmark execution. No product execution code or correctness/isolation/cleanup gate changed. The ranked optimization experiments remain in [the workstream plan](../../../docs/edgy-performance.md).
