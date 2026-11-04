# Native capture reconciled against exact mutant identities

[Run 41892806213](https://github.com/RuiCostafrg/oximut/actions/runs/41892806213) captured head `7ae4b25` via merge revision `f9aaa3ce87025679a0628146acbe19e36d2d884a` on Ubuntu 24.04 x86_64. Both the validator and controller preload match the native build's source hashes.

Exact workload qualification and current capture validation pass: 41 sources, 102 tests, five mutation files and ten identical killed arithmetic edits. The capture contains one controller, 14 unique serial commands (initialize, two baselines, ten mutants, close), 18 observed fork lifecycles and one finalized first-mutant CPU profile. Every captured mutant digest matches one unique executed mutant in the retained qualification report; counts also reconcile. Event sequences are contiguous, write-failure flags are clear and all reply observations follow normal local send returns. Earlier captures remain unchanged as historical raw evidence and fail the current identity/integrity gates.

## Sampling scope and job observations

The first mutant's request-observation-to-local-send-return interval is **430.056 ms**. Profiler activation acknowledgment arrives **87.297 ms** after request observation; acknowledgment-to-send-return is **342.760 ms**. These boundaries observe neither recipient delivery nor callback completion. CPU sampling begins during profiler activation, omits some request prefix and may include inspector overhead; acknowledgment latency is not an exact unsampled duration. The CPU profile describes the controller alone, includes idle frames and excludes fork CPU. It is not a full-request or process-tree CPU partition.

Independent mutant boundary medians (not additive; raw per-job intervals and mutant IDs are in `boundary-intervals.json`):

| Interval | Median ms |
|---|---:|
| Request observation to local send return | 256.593 |
| Request observation to fork | 5.899 |
| Fork to started response | 116.640 |
| Run dispatch observation to collection RPC | 40.048 |
| Collection to testfileFinished | 78.622 |
| testfileFinished to local controller send return | 7.250 |

## Resources (one profiled run, overhead-bearing)

| Tool | Wall s | CPU s (user / sys) | Peak aggregate RSS bytes | Peak / observed processes |
|---|---:|---:|---:|---:|
| vitest | 1.395112 | 1.820111 (1.491361 / 0.328750) | 280514560 | 2 / 5 |
| oximut | 5.730540 | 8.391264 (5.671459 / 2.719805) | 330182656 | 4 / 21 |
| stryker | 5.120281 | 6.686881 (6.000740 / 0.686141) | 566480896 | 4 / 4 |

Paired ratios and bootstrap 95% CIs are **not available**: this diagnostic has one run, no warmup and profiler overhead. It cannot establish product gains or target achievement. The [qualified same-engine control](../2026-11-04-native-control/README.md) retains repeated measurements and paired CIs; its after/before intervals include 1. These profiled timings must not be subtracted from other hosts/campaigns to infer savings.

`resources.phaseWallMs`:

| Phase | Wall ms |
|---|---:|
| finalInputValidation | 0.000120 |
| inputAndSchedulerPreparation | 3.736246 |
| instrumentedBaseline | 1062.179485 |
| mutationExecution | 3103.567296 |
| mutationPlanning | 0.088205 |
| originalBaseline | 1500.143452 |
| postBaselineValidation | 0.000160 |
| teardown | 50.991159 |

## Retained evidence and revalidation

This capsule retains unmodified native timeline/profile files, diagnostic summary, qualification summary/measurement rows, full Oximut report and build metadata. Derived revalidation and boundary intervals are identified separately. `SHA256SUMS` covers every retained file except itself. The source archive, prepared projects, binary, other tools' reports and logs remain only in the workflow artifact with configured 14-day retention; committed hashes cannot reconstruct omitted files after expiry or rerun the workload without them.

From the repo root, rerun the stored evidence checks:

```sh
PYTHONPATH=benchmarks python3 - <<'PYTHON'
from pathlib import Path
from profile_native import controller_capture_inventory, qualification_errors
root = Path('benchmarks/results/2026-11-04-controller-mutant-identities')
assert not qualification_errors(root / 'qualification', 0)
capture = controller_capture_inventory(root / 'profiles', root / 'qualification')
assert not capture['errors'] and capture['workload_reconciled']
PYTHON
```

This revalidates retained evidence rather than executing a new benchmark. No product execution code or correctness/isolation/cleanup gate changed. The next experiment remains an opt-in warm runtime with a proven fresh realm and dependency graph, as ranked in [the workstream plan](../../../docs/edgy-performance.md).
