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

## Oracle batch sorting with the offline snapshot

Three qualified snapshot traces model **1.14–1.35 s** less mutant batch
wall even with advance knowledge of each fork's observed request-to-exit
duration. The oracle sorts these durations within each controller and
minimizes the sum of four-worker batch maxima. Perfect fractional
packing would save only another **0.16–0.25 s** under the same fixed
duration assumption. The model omits gaps outside the mutant batches
and cannot predict contention after reordering. This is a reason to
prioritize removing worker CPU over spending the remaining wall gap on
a batch ordering heuristic.

```sh
base=/root/oximut-evidence/vitest-fork-bundle
for path in snapshot-native-source-io/artifact/snapshot-offline-capture \
            snapshot-native-project/artifact/snapshot-offline-capture \
            snapshot-native-execution-cpu/artifact/execution-cpu-capture; do
  python3 benchmarks/results/2026-11-20-rolling-dispatch/replay.py "$base/$path/profiles"
done
```

## Native score and decision

The five measured before/after pairs on Ubuntu 24.04 x86-64 with four
matched workers show **3.93% slower wall**, **4.27% lower CPU**, and **6.01%
higher sampled peak aggregate RSS** for the rolling prototype. The paired wall
ratio's 95% bootstrap interval was **1.033–1.040**. Median mutation execution
increased from **14.51 s to 15.03 s**. Keep batch dispatch as the default.

On this runner the stock comparison with Stryker was **1.009× wall, 0.896×
CPU, and 0.647× RSS**. Rolling scored **1.048×, 0.859×, and 0.690×**.
These are host-specific pairs. The earlier **1.077× / 0.954× / 0.653×**
campaign ran on a different runner allocation. Every row passed the
historical cross-check, including exact outcomes for 206 mutants, 102
baseline tests, four workers, and the same count of whole-file selections.
The native fork fixture independently exercised rolling admission, crash
attribution, fresh PIDs, and cleanup. Unprofiled measurements contain no
per-fork trace, so the specific scheduler effects that offset the modeled
gain remain unmeasured.

The newer stock pairs require at least **13.90 CPU-seconds less work**
at the median to reach half Stryker's wall with four logical CPUs, even
at full utilization. The earlier scored machine required **23.69 CPU-seconds**.
The estimate is Oximut CPU seconds minus twice the paired Stryker wall
seconds. It shows why reducing idle time alone cannot close the gap.

- [Native score run](https://github.com/RuiCostafrg/oximut/actions/runs/44735321991)
- [Verified score and build provenance](score.json)
- Raw score archive and GitHub metadata at
  `/root/oximut-evidence/rolling-dispatch/score`.

```sh
gh workflow run real-project-benchmark.yml --ref t3/fresh-worker-startup \
  -f rolling_lanes=true -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-rolling-dispatch/score.py \
  /root/oximut-evidence/rolling-dispatch/score \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture/qualification/measurements.jsonl
```
