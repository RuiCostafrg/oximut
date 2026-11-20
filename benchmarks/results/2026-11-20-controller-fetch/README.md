# Controller fetch reuse in fresh Vitest forks

**Keep the shared transform path as the default.** The lane-local experiment removed 91% of first-collection fallbacks in the diagnostic. The unprofiled paired wall and CPU medians were within 0.2% of the shared control, with intervals spanning parity. The switch remains available for reproducing the experiment.

## Mechanism

The historical capture had 154 empty snapshots in 206 mutant forks. Lane 1 had four empties in 52 forks. Lanes 0, 2 and 3 had 150 empties in 154 forks. The distribution is consistent with the lane that wins a fetch doing work in its own graph while other lanes receive the artifact through Oximut’s `TransformShare`.

In Vitest 5.1.0, the fork requests a snapshot on its first dependency lookup and keeps that response for the lifetime of the runner. The controller walks the requesting lane’s Vite graph from the selected test files and setup files. A reachable transformed node contributes entries once its temporary artifact is recorded on the transform result as `__vitestTmp`. Vitest may emit both the URL and the resolved ID for that one node. Environment-local externalization entries can also be present. A `TransformShare` hit returns the first fetch’s metadata and temp path without executing Vitest’s transform service in the requesting lane. That lane can lack a test root, dependency edges, or a materialized dependency even while a valid artifact exists elsewhere.

Entrypoint fetches bypass the snapshot. For dependencies, the worker applies its mock handling before looking up the ID and its unwrapped form in the snapshot. A missing key or a failed read of its artifact falls back to `fetch`. The controller can serve the fallback from `TransformShare`, execute the native fetch path and reuse the Vite graph’s existing temp path, or execute the native path and materialize a file. The fork transport uses temporary files with `fsModuleCache: false`. Vitest keys the path by its temp project directory, environment and resolved ID, and atomically writes transformed code with its inline map and cache metadata. Hard invalidation clears the graph’s transform result and its temp path reference, so a later fetch can rewrite the same path. All three paths use the same temporary-file reply shape.

## Experiment

The opt-in `OXIMUT_EXPERIMENT_LANE_LOCAL_FETCH=1` switch bypasses `TransformShare` for shared fork lanes. Each lane takes the stock fetch path, builds its own Vite graph and reuses it for later fresh forks under the same resident inputs. The graph gets its code, source maps, static mock metadata, and edges from the native transform and resolution pipeline. The workers use the stock protocol, exact mutation selectors, whole-file lists and project mock and resolver configuration. Resident code or map changes clear the shared cache and hard-invalidate every lane graph. The compact IPC fixture kept the same outcomes when it changed the source map and then restored it, with both switches exercised through the fetch probe.

The controller probe records its pre-fetch graph lookup, actual share and temp-path decisions, and successful write boundaries. It joins to first-collection worker spans by controller PID, fork PID and RPC ID. A graph-hot count means the requesting lane’s graph had a transform result at that lookup. Writes count completed Vitest saves. Rewrites count a second or later save to the same path in the same controller, and the summary identifies which of those were in the same resident generation. Snapshot graph counts describe lane-local nodes, and key counts include aliases. Controller save and worker IPC wait intervals overlap, so their sums describe work across calls and workers.

## Native diagnostic capture

The control and candidate ran serially on one Ubuntu 24.04 x86-64 allocation with four usable CPUs and four matched workers. Both matched the exact 206 historical mutant identities and outcomes (202 killed, four survived), the exact 102 baseline test identities and complete attribution. The trace reconciled 52 compact batches, 206 fresh mutant forks and eight baseline forks per variant. It independently verified exact fork selectors, whole-file paths (204 selected-file and two full-suite mutants), and clean exits. Each variant used two controllers, with one resident context kept to closure in each.

| Through first collection | Shared control | Lane-local |
| --- | ---: | ---: |
| Empty snapshots out of 206 | 154 | 0 |
| Fallback fetches | 8,145 | 724 |
| Fallbacks served by `TransformShare` | 8,061 | 0 |
| Fallbacks reusing a native temp path | 0 | 391 |
| New temporary-file saves | 84 | 333 |
| Rewrites to a previously saved path | 0 | 0 |
| Fetch IPC in flight, summed over all forks | 5.57 s | 3.52 s |
| Fetch IPC in flight, summed over 52 last finishers | 1.46 s | 0.82 s |
| First-collection wall, summed over the last-finishing paths | 3.96 s | 3.58 s |
| Worker CPU through first collection | 10.75 s | 9.07 s |
| Controller self CPU inside mutant requests | 3.93 s | 3.92 s |

All 154 empty control snapshots had zero roots in the requesting lane’s graph, and each followed a `TransformShare` hit for one of the requested test roots. The control issued 412 entry fetches, 6,020 dependency fetches after an empty snapshot, and 1,713 after a nonempty snapshot. None of the latter had a matching key in the memoized reply. The control’s 99.0% `TransformShare` hit rate is conditional on a fallback fetch. It measures receipt of shared metadata and an existing temp path, while the snapshot measures what the requesting lane’s graph can serve.

Lane-local also issued 412 entry fetches. Its 312 remaining dependency fetches all lacked snapshot keys and wrote fresh artifacts. It sent 91% fewer fallbacks while doing roughly four times as many writes, with 3.87 MB of transformed code submitted to writes versus 1.06 MB. The first batches in the two cold controllers both issued 323 fallback fetches. Their summed fetch IPC time rose from 1.37 s to 2.95 s. Later batches went from 7,822 to 401 fallback fetches and from 4.20 s to 0.57 s of summed fetch IPC time. Cold lane duplication is a real cost, especially when a controller runs few jobs.

The scale workload kept its resident image constant across batches, so there were no same-path rewrites. In the compact resident-map fixture, two map changes triggered two hard invalidations per variant. The fixture observed four shared-control and 12 lane-local same-path rewrites, all after generation changes, and preserved the expected outcomes. A separate functional fixture passed with both modes and exercised alias resolution, a configured package import condition, a per-file mock, `vi.importActual`, and an unmocked test file across fresh forks. Its local arm64 timing was excluded.

The initial native attempt is retained as a failed capture. A probe event read Vitest’s local `cached` variable before its initialization. The corrected source passed the resident-map fixture before the qualified native capture. [Initial run](https://github.com/RuiCostafrg/oximut/actions/runs/44694947209) · [Qualified diagnostic run](https://github.com/RuiCostafrg/oximut/actions/runs/44695560151)

The qualified source is `28bfcb2ebdef6c56f685274833ecba845a1b6850`. The release binary SHA-256 is `03bb307477640252eca6837415ec45be49dd407fa522b91b29e7f3328c0eea3f`, matching the startup capture. The artifact and log ZIPs, extracted controller and worker records, qualification reports and recovery verification are retained at `/root/oximut-evidence/controller-fetch/native`. The diagnostic comparison justifies an unprofiled paired score because it removes repeat fetches and reduces last-finisher collection wall and worker CPU even with the larger warmup cost. [Diagnostic rollup](summary.json)

## Unprofiled paired score

The native Ubuntu 24.04 x86-64 campaign at four matched workers ran one warmup pair and six measured pairs. Variant and tool order alternated across rounds. The shared control used the exact `1059dd7` adapter. The candidate used the opt-in fork fetch change from `081d08e96f4ff4f1f65f81947f191fd0e35e3340`. Both used a single release executable built before timing. The scored workload gate and historical cross-check covered all 42 measurements, including warmups. The diagnostic capture supplies independent per-fork selector and dispatch evidence.

| Compared with | Wall | CPU | Sampled peak aggregate RSS |
| --- | ---: | ---: | ---: |
| Lane-local vs shared | 1.001× [0.979, 1.024] | 0.999× [0.979, 1.015] | 1.022× [0.974, 1.080] |
| Shared vs same-round Stryker | 1.075× [1.051, 1.083] | 0.945× [0.926, 0.957] | 0.659× [0.634, 0.674] |
| Lane-local vs same-round Stryker | 1.079× [1.071, 1.111] | 0.949× [0.938, 0.967] | 0.673× [0.648, 0.688] |
| Targets vs Stryker | 0.50× | 0.80× | 0.80× |

Values are medians of paired ratios with 95% percentile bootstrap intervals from 10,000 draws. CPU is reaped process-tree user plus system CPU. RSS is the sampled peak sum across the live process tree. The result is specific to this host and corpus. Lane-local misses the wall and CPU targets and meets the RSS target. All lane-local versus shared intervals include parity. The diagnostics identify duplicated cold transforms and materialization as costs that can offset steady-state fetch savings. They also found zero same-generation rewrites to avoid.

The prior unprofiled study remains **1.077× wall, 0.954× CPU, and 0.653× sampled peak aggregate RSS** versus Stryker. This campaign compares its variants using its own paired measurements. The archive, logs, measurements, reports and recovery verification are retained at `/root/oximut-evidence/controller-fetch/score`. [Unprofiled native run](https://github.com/RuiCostafrg/oximut/actions/runs/44696471360) · [Paired score verification](score-verification.json)

## Reproduction

Run from the repository root on Ubuntu 24.04 x86-64 with four usable CPUs and the repository’s pinned Node and Rust. Diagnostic probes add overhead and are kept separate from scored runs.

```sh
npm ci --ignore-scripts
cargo build --locked --release -p oximut-cli
output=$(mktemp -d)
env -u OXIMUT_EXPERIMENT_LANE_LOCAL_FETCH python3 benchmarks/profile_native.py \
  --binary target/release/oximut --worker-phases --startup-contract --first-collection \
  --controller-fetch --workload colord-scale --concurrency 4 --output "$output/capture"
OXIMUT_EXPERIMENT_LANE_LOCAL_FETCH=1 python3 benchmarks/profile_native.py \
  --binary target/release/oximut --worker-phases --startup-contract --first-collection \
  --controller-fetch --workload colord-scale --concurrency 4 --output "$output/local"
python3 benchmarks/results/2026-11-20-controller-fetch/summarize.py "$output" \
  /root/oximut-evidence/fresh-worker-startup/combined/artifact/capture "$output/summary.json"
```

Run the unprofiled reproduction on an otherwise idle native runner. Both variants use one release binary built before timing. The script archives the historical adapter from Git and uses the current adapter with the lane-local switch.

```sh
bash benchmarks/results/2026-11-20-controller-fetch/score.sh target/release/oximut \
  "$output/score" "$output/baseline-adapter"
python3 benchmarks/results/2026-11-20-controller-fetch/verify-score.py "$output/score" \
  /root/oximut-evidence/fresh-worker-startup/combined/artifact/capture "$output/score-verification.json"
```

Recover the original GitHub artifacts, raw logs and source inventories with the startup capture’s recovery script.

```sh
recovery=benchmarks/results/2026-11-19-fresh-worker-startup/recover.sh
bash "$recovery" 44695560151 /root/oximut-evidence/controller-fetch/native
bash "$recovery" 44696471360 /root/oximut-evidence/controller-fetch/score
```
