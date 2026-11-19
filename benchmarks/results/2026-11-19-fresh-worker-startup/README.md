# Fresh Node and Vitest startup on colord-scale

**Measure one targeted reduction in Vitest’s eager import graph next.** With a populated compile cache, the fresh stock Vitest fork used **50.5–58.0 ms more CPU** than the fresh minimal IPC fork across two native Ubuntu x86-64 captures. The median stock start handler consumed about **1 ms CPU**. The excess was almost entirely before it received `start`, after the first common preload. Keep the per-slot compile cache. Populating it recovered about **16–17 ms CPU per stock fork** relative to disabling it, and about **23 ms** relative to opening a new cache directory for each fork.

## Startup replay

The table uses the combined capture. Each cell has 48 fresh forks in 12 four-fork waves, with rotating cell order. The eight additional priming forks are excluded. All times are medians in milliseconds. User, system, and total CPU medians are calculated independently.

| Entrypoint | Cache | Fork → started wall | Wall p90 | User CPU | System CPU | Total CPU |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Minimal IPC | Cold | 29.1 | 30.5 | 20.7 | 6.6 | 27.3 |
| Minimal IPC | Populated | 28.6 | 30.2 | 21.1 | 5.5 | 26.7 |
| Minimal IPC | Disabled | 28.7 | 30.7 | 21.5 | 6.1 | 26.9 |
| Stock Vitest | Cold | 102.5 | 108.1 | 87.4 | 12.2 | 100.1 |
| Stock Vitest | Populated | 79.2 | 84.0 | 64.6 | 13.3 | 76.8 |
| Stock Vitest | Disabled | 96.5 | 98.1 | 81.5 | 12.0 | 93.3 |

- In the populated cell, the two entrypoints reached the same first preload at about **24 ms process CPU**. From preload to handling `start`, Vitest used **52.3 ms CPU** and the control **2.8 ms**. From handling `start` to sending `started`, Vitest used **0.91 ms** and the control **0.05 ms**. A separate startup-only capture reproduced the ordering on a different runner. Its populated medians were **91.5 ms wall / 87.9 ms CPU** for Vitest and **31.5 ms wall / 29.5 ms CPU** for minimal IPC.
- The replay launches the actual installed Vitest entrypoint with the runtime-captured `execArgv` on both workers. These include `--experimental-import-meta-resolve`, the Vitest warning preload, `node` and `development` conditions, and Oximut’s compile-cache flush preload. It uses the real cwd, Node test environment, advanced IPC serialization, piped stdio, and the start settings consumed before `started`. A shared diagnostic preload marks first JS, receipt of `start`, and send of `started` and `stopped`. The parent sends `stop`, expects its acknowledgement, sends SIGTERM, and waits for exit. Wall goes through exit. Per-fork user/system counters extend through the `stopped` send, so CPU in later termination is outside those counters.
- Cold means an empty compile-cache directory per fork. Populated means four privately held cache directories per entrypoint, each primed with one identical start/stop fork and reused only for that lane in the replay. Disabled uses `NODE_DISABLE_COMPILE_CACHE=1` with the same launch flags and an empty directory. The stock cache contained 46 files and about 373 KB after priming. The disabled directory stayed empty. Cold stock startup was 6–7 ms CPU slower than disabled. Cold stop also used more CPU, consistent with cache creation and flush. The replay measures startup only and does not establish a production cache hit rate, or an end-to-end speedup from changing cache policy.
- The minimal fork estimates the cost of Node boot plus preloads, a small ESM entry and the same IPC handshake. The stock-control difference localizes the cost to the Vitest import and initialization path. It includes loader file reads, registration, V8 work and instrumentation. The preload checkpoint is the earliest instrumented JS, and the probe’s early IPC listener can affect delivery timing. The experiment neither drops the OS page cache nor identifies how much of the import phase any single dependency accounts for.

## First collection on the qualified workload

The combined run went through the normal Oximut CLI and stock fork pool. Across 206 mutant forks, startup used **16.05 s CPU**, then work through first collection used **10.46 s CPU**. Last-finishing paths totaled **4.82 s** and **3.89 s wall** for those phases. The worker-side boundary from receipt of `run` to sending first `onCollected` gives the following breakdown.

| Worker-side measure | Median per mutant fork | Sum over 206 forks | Sum over 52 last-finishing workers |
| --- | ---: | ---: | ---: |
| Run receipt → first collection wall | 67.1 ms | 14.77 s | 3.81 s |
| At least one module fetch RPC in flight | 19.9 ms | 5.28 s | 1.43 s |
| Wall outside module fetch IPC intervals | 46.6 ms | 9.49 s | 2.38 s |
| Synchronous inlined-module compilation wall | 6.1 ms | 1.24 s | 0.31 s |
| Synchronous inlined-module compilation thread CPU | 3.0 ms | 0.61 s | 0.14 s |

- **Fallback fetch dominated fetch wait.** There were **7,750** `fetch` RPCs through first collection. Every reply directed the worker to a temporary file. Median latency was **0.24 ms**, p90 **1.73 ms**, and summed intervals **5.17 s**. There were **206** `fetchWarmModules` RPCs, totaling **0.108 s**, and **154** returned no keys. Snapshot keys can alias the same module, so response size does not give a hit rate. The **412** `resolve` RPCs are separately reported and sum to **0.71 s** of round-trip intervals. They are outside the fetch column.
- The first eight mutant forks in their controllers had a median **152.9 ms** with a fetch in flight versus **19.7 ms** for the other 198. The corresponding median wall outside fetch intervals was **47.1 vs 46.6 ms**. The earlier trace’s 8,390 `fetch` calls cover the complete mutant lifetime. This capture measures a different window and groups `fetchWarmModules` separately.
- The raw spans distinguish warm graph snapshot and fallback round trips, and the summary unions overlapping intervals per worker. Round trips include controller queueing, resolution/transform/cache service, serialization and worker receipt. A temporary-file reply does not indicate whether the controller transformed code or reused a transform, and it excludes the following worker file read. Work can overlap another request’s wait. Across workers these spans overlap, including in the sums over all forks.
- The compile probe times stock Vitest’s synchronous `vm.runInThisContext` call for wrapped inlined code. It counts **8,375** calls through first collection. It leaves the returned function and its invocation intact. The Vitest evaluator’s own inline “self time” sums to **10.01 s wall**. It still includes waits around imports and can overlap the fetch intervals. Native external-module loads, subsequent JS execution, test registration, local file reads and scheduling remain mixed. Wall outside fetch intervals cannot isolate pure evaluation.

## Ranked decision

1. **Test removal of unused eager Vitest imports.** Begin with a diagnostic ablation that moves the default benchmark provider’s static `tinybench` import behind its async benchmark-only dispatch. It is reachable from the shipped stock fork entrypoint, although colord-scale runs tests only. Use the same checkpoints to measure the ablation and total phase work through first collection before widening it to other optional imports. The measured 50–58 ms populated gap covers the whole graph and sets the scale of the opportunity. The capture assigns no share of that gap to `tinybench`. Preserve full Vitest behavior and score a production candidate only if a qualified change removes work through collection.
2. **Recover miss-heavy graph reuse within a live controller.** Stock snapshot requests were cheap and frequently empty, while the fallback path occupied 1.43 s of the last-finishing paths including snapshots. Verify which invalidations and temporary-file rewrites drive misses, then test reusing artifacts whose transformed code, maps, conditions, and mock context are identical in that controller generation. Keep mutation-dependent content isolated and invalidated. First-batch latency is visibly higher, but steady batches also accounted for **3.93 s** of aggregate in-flight fetch time.
3. **Retain compile-cache reuse and deprioritize Node boot, the start handler, and inlined compilation as standalone changes.** The cache stabilizes fresh startup. Node boot is a common floor under the fresh-process constraint. The start handler and measured synchronous compilation have less headroom than the eager graph and miss path. The stop-time counters also make a cache-preseed or flush-policy change a lower priority than reducing the repeated warmed import cost.

The previous unprofiled paired score remains **1.077× Stryker wall, 0.954× CPU, and 0.653× sampled peak aggregate RSS**. Meeting **0.50× / 0.80× / 0.80×** from that score requires about **54% less candidate wall** and **16% less CPU**, with RSS already below target. The whole measured startup phase was only about 31% of the earlier 15.55 s diagnostic command wall. Startup improvement alone cannot close that wall gap. Score an optimization with alternating unprofiled four-worker pairs and uncertainty intervals after the exact-workload gate passes. [Unprofiled paired campaign](../2026-11-19-lane-resident-transport/native-campaign.md)

## Qualification and provenance

Both captures used Ubuntu 24.04 x86-64, Node **26.3.1** with V8 **14.6.202.34-node.20**, Vitest **5.1.0**, Stryker **10.1.1**, and Rust **1.96.0**. Both rebuilt the same release binary as the prior worker-phase capture. The binary SHA-256 is `03bb307477640252eca6837415ec45be49dd407fa522b91b29e7f3328c0eea3f`. GitHub artifact digests, ZIP CRCs, build source manifests, archived source against Git, helper hashes, and adapter hashes were verified. The combined run exposes four logical CPUs on an Intel Xeon 6975P-C. The startup-only run used an independent Azure-backed runner.

Both workload captures matched the prior **206 exact mutant identities and outcomes** (202 killed, four survived), **102 test identities**, and complete baseline attribution. The combined trace independently matched the environment selector of every fresh mutant worker to its batch and the report, and verified that run requests contained only whole-file paths (204 selected-file mutants and two full-suite mutants). Each capture reconciled 52 compact resident batches and eight baseline forks. The qualification reported clean lifecycle/exit and no remaining descendants requiring cleanup. The minimal control ran only the IPC start/stop replay.

- Startup-only source `6043d55b3960b8c65b846690d47a5e9539be99dd`. [Native run](https://github.com/RuiCostafrg/oximut/actions/runs/44632424160)
- Combined source `b3c1cd94e3c8fb2433f9f001dd2887ab7a8d229e`. [Native run](https://github.com/RuiCostafrg/oximut/actions/runs/44632835828)
- [Machine-readable rollup](summary.json). Raw archives, logs and unpacked captures are retained under `/root/oximut-evidence/fresh-worker-startup/{startup-only,combined}`. Each contains `artifact/startup-replay.json` with every fork’s wall boundaries, user/system counters and cache counts. The combined capture also contains per-worker module spans, synchronous compile and evaluator observations, the controller timelines and all qualification reports.

## Reproduction

On native Ubuntu 24.04 x86-64 with at least four usable CPUs, use the pinned toolchain above and run from the repository root:

```sh
npm ci --ignore-scripts
cargo build --locked --release -p oximut-cli
output=$(mktemp -d)
python3 benchmarks/profile_native.py --binary target/release/oximut \
  --worker-phases --startup-contract --first-collection \
  --workload colord-scale --concurrency 4 --output "$output/capture"
node benchmarks/profiling/fresh-worker-startup.mjs "$output/capture/profiles" "$output"
```

To run the native CI capture from the combined source on its diagnostic ref:

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=fresh-worker-startup -f workload=colord-scale -f concurrency=4
```

To recover and reproduce the recorded rollup using the retained prior capture:

```sh
result=benchmarks/results/2026-11-19-fresh-worker-startup
evidence=/root/oximut-evidence/fresh-worker-startup
bash "$result/recover.sh" 44632424160 "$evidence/startup-only"
bash "$result/recover.sh" 44632835828 "$evidence/combined"
python3 "$result/summarize.py" "$evidence/startup-only" "$evidence/combined" \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture "$result/summary.json"
```
