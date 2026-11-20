# Rolling dispatch trace replay

Both qualified native four-worker traces modeled a **1.11–1.30 s** gain when charging each next fork its observed request-to-fork delay, or **1.94–2.13 s** with immediate fork admission. These are counterfactual savings against the sum of observed mutant request-to-reply windows, about **8–10%** and **15–16%** of those windows, respectively. The traces contained **5.12–5.74 slot-seconds** of idle capacity after early forks exited and before each batch's last exit, including the unused slots in the partial final batches.

The replay retains trace order and controller boundaries. It adds each fork's recorded service time to the earliest free slot. The two models differ in whether service starts at the original fork or at its batch request. Both omit batch reply overhead inside the replay and leave gaps between requests, controller turnover, and work outside the mutant windows out of the comparison. These scenarios assume unchanged service times under different contention. They neither partition the causal cost of the barrier nor predict an unprofiled score.

The estimate warrants a native prototype. Keep the shared transform path and admit rolling work only within a compatible resident generation. Each slot becomes available after its existing fork has exited and result attribution is complete. Drain all slots before any resident image change. Preflight the adapter's batch-wide file state, selector assignment, pool close and cancellation paths before lifting the barrier. Qualify the exact 206 mutant identities and outcomes and 102 tests against stock dispatch, then run alternating unprofiled pairs at four workers.

The lane-local fetch ablation remains useful as evidence. The qualified switch duplicated temporary-file saves, from 84 to 333, and the scorable wall and CPU medians remained within 0.2% of shared transforms. Preserve its report and opt-in reproduction on the experiment branch.

Run from the repository root:

```sh
dir=benchmarks/results/2026-11-20-rolling-dispatch
python3 "$dir/replay.py" /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture/profiles
python3 "$dir/replay.py" /root/oximut-evidence/fresh-worker-startup/combined/artifact/capture/profiles
```
