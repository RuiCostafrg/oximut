# Lazy tinybench in Vitest fresh forks

**Keep the patch as a diagnostic.** Deferring tinybench saved **142–156 ms of worker CPU through first collection** across 206 fresh mutant forks per capture. That is **0.69–0.76 ms per fork**, or **0.53–0.58%** of the corresponding stock worker CPU. About 77–80% of the saving was already present when the worker sent `started`. CPU between `started` and first collection also fell slightly in both pairs.

## Diagnostic results

Each cell reports stock → lazy, with the lazy-minus-stock change in parentheses. The first pair ran stock first and the second ran lazy first.

| Oximut phase | Pair 1 | Pair 2 |
| --- | ---: | ---: |
| Worker CPU through `started` | 16.214 → 16.094 s (−121 ms) | 16.160 → 16.047 s (−113 ms) |
| Worker CPU from `started` through first collection | 10.652 → 10.617 s (−35 ms) | 10.599 → 10.570 s (−28 ms) |
| **Worker CPU through first collection** | **26.866 → 26.710 s (−156 ms)** | **26.759 → 26.617 s (−142 ms)** |
| Last-finishing paths, fork → `started` | 5.001 → 4.790 s (−211 ms) | 4.864 → 4.777 s (−87 ms) |
| Last-finishing paths, run dispatch → first collection | 3.999 → 3.834 s (−165 ms) | 3.868 → 3.857 s (−11 ms) |
| **Last-finishing paths, fork → first collection** | **9.006 → 8.630 s (−376 ms)** | **8.737 → 8.639 s (−97 ms)** |
| Instrumented Oximut command wall | 16.238 → 16.042 s (−196 ms) | 15.969 → 15.973 s (+4 ms) |

Worker CPU sums the user and system counters of all mutant forks. Wall phase sums use the worker that finished the test file last in each of 52 batches. The full fork-to-collection wall includes the dispatch delay. First-collection fallback fetch counts varied from 7,746 to 8,162 across captures and synchronous compilation counts varied from 8,333 to 8,375. In both pairs, aggregate fetch-in-flight wall fell while wall outside those intervals rose. Those intervals overlap other work and are sensitive to scheduling and queueing. The experiment establishes a small startup CPU saving through collection. It cannot assign the wall changes to a specific loader, RPC, or evaluation cost. Two instrumented pairs quantify the observed repeatability and leave end-to-end uncertainty unresolved.

## Decision and score

The startup CPU saving was **0.55–0.59 ms per fork**. For scale, the prior populated stock-vs-minimal startup replay found a **50–58 ms** CPU gap across the entire eager Vitest path. The ablation used the real workload and different checkpoints, so the values give scale rather than a component decomposition. Combined with the mixed command wall result, the CPU saving is too small to justify maintaining a Vitest patch or running an unprofiled scoring campaign for it. Defer widening the ablation until a specific remaining eager import has measured headroom. Graph reuse is a separate experiment.

The prior unprofiled Oximut/Stryker score remains the reference at **1.077× wall**, **0.954× CPU**, and **0.653× sampled peak aggregate RSS** against targets of **0.50×**, **0.80×**, and **0.80×**. Wall and CPU missed their targets. The diagnostic phase deltas cannot update those ratios. [Prior native paired campaign](../2026-11-19-lane-resident-transport/native-campaign.md)

## Experiment

The benchmark-only patch moves `Bench` into `await import('tinybench')` inside Vitest 5.1.0’s async default benchmark provider. Six generated bare imports also reference tinybench. The experiment removes those edges from the installed distribution, including the one in `dist/workers/forks.js`. A separate Node load-hook check imports the real fork entrypoint in a fresh process. It records one tinybench module load for stock and for the provider-only edit, and zero for the complete patch. This check runs after the workload captures. The smoke uses `vitest bench` with a single fresh fork and requires a completed, named benchmark with positive timing samples. The package lock and the Oximut binary and adapter are common to all four captures.

The sequence was stock → lazy → lazy → stock on one native Ubuntu x86-64 runner. Each adjacent pair puts a different arm first. Each capture runs plain Vitest, Oximut and Stryker serially in fresh project directories. The diagnostic comparison uses the Oximut observations. The patch applies to the installed Vitest used by all three tools within each lazy qualification run. The diagnostics preserve four matched workers, fresh forks, exact selectors and whole-file dispatch.

All four captures passed the fresh-fork lifecycle and historical workload checks. Each matched all **206 exact mutant identities and outcomes** (202 killed and four survived) and **102 test identities** with complete baseline attribution. Traces reconciled **52 compact resident batches**, **206 fresh mutant forks**, and **eight baseline forks** per run. The selector matched the exact mutant and the dispatch contained whole-file paths only for 204 selected-file and two full-suite mutants. Forks stopped cleanly, controller exits were normal, and the harness reported no descendant cleanup. All four native benchmark-mode smokes passed. The workflow’s 176 benchmark tests (one skipped) and 48 profiling tests passed.

The native runner was Ubuntu 24.04 x86-64 on an Intel Xeon 6975P-C with four visible CPUs. The release executable SHA-256 matched the previous startup capture (`03bb307477640252eca6837415ec45be49dd407fa522b91b29e7f3328c0eea3f`). The retained artifact covers stock and patched installed-file hashes, the lock hash, and raw reports and logs. Recovery verified the GitHub artifact digest and size, both ZIP CRCs, source against the archived Git revision, the binary hash, and all measured helper and adapter hashes. [Native workflow](https://github.com/RuiCostafrg/oximut/actions/runs/44695619417) · [Machine-readable results](summary.json)

## Reproduction and evidence

Run from the repository root on native Ubuntu x86-64 with Node 26.3.1, Rust 1.96.0 and at least four usable CPUs.

```sh
npm ci --ignore-scripts
cargo build --locked --release -p oximut-cli
output=$(mktemp -d)
python3 benchmarks/profiling/tinybench-ablation.py target/release/oximut "$output/ablation"
```

The native workflow builds and archives the candidate, runs the benchmark and profiling harness tests, captures all four runs, and uploads the binary, stock/lazy hashes, installed patch, raw checkpoints, reports and logs. Use a private dependency installation for local runs because the harness temporarily edits and restores installed Vitest files.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/oximut-eager-startup-ablation \
  -f capture_mode=tinybench-ablation -f workload=colord-scale -f concurrency=4
```

The recorded workflow ran from commit `43c89c3`. Recover it and compare the raw captures to the historical outcomes and launch selections.

```sh
result=benchmarks/results/2026-11-20-eager-tinybench
evidence=/root/oximut-evidence/eager-tinybench
historical=/root/oximut-evidence/fresh-worker-startup/combined/artifact/capture
bash "$result/recover.sh" 44695619417 "$evidence"
python3 "$result/summarize.py" "$evidence" "$historical" "$result/summary.json"
```
