# Colord-scale bottlenecks after compact lane transport

Fresh-fork startup and the work before first test collection accounted for **26.40 s
of CPU** across 206 mutant workers and **8.51 s of wall time** along the
last-finishing workers’ paths. That is 59% of the 45.00 s whole-command CPU and
55% of the 15.55 s whole-command wall time in the same diagnostic capture.
Startup is the largest separately measured phase. The next experiment should
split startup into Node process boot and Vitest initialization, then score one
change that removes work while keeping fresh workers.

## Qualification

Main was `b451b6563d44bb3e718c52104580031e00911bde`. The tested diagnostic
commit `206a9538fda24084e96e3e10c6540116932c2012` changes only the benchmark
harness, its fixtures, and the capture workflow. The two Ubuntu 24.04 x86-64
runners produced byte-identical release binaries. They ran Node 26.3.1,
Vitest 5.1.0 and Stryker 10.1.1 at four matched workers.

Both captures passed qualification with exact identities and outcomes for
206 mutants (202 killed, four survived), 102 test identities and complete
baseline attribution. The worker trace reconciled 206 distinct mutant forks,
eight baseline forks and 52 mutant batches. All 52 used compact resident
transport. Both complete artifacts, source inventories, selected adapter bytes,
binaries and workflow log archives were verified after download.

| Independent capture | Oximut wall | Oximut CPU | Sampled peak RSS |
| --- | ---: | ---: | ---: |
| [Fork and controller phases](https://github.com/RuiCostafrg/oximut/actions/runs/44627901977) | 15.55 s | 45.00 s | 760 MB |
| [Rust resource accounting](https://github.com/RuiCostafrg/oximut/actions/runs/44627902118) | 26.73 s | 70.60 s | 810 MB |

These are single diagnostic observations with instrumentation overhead and
different runner allocations. Use the earlier paired campaign for performance
ratios. CPU totals include process-tree user and system time. Sampled peak RSS
can miss short peaks. The largest observed Oximut sampling gaps were 59 ms and
126 ms, respectively.

## Worker bottlenecks

The wall-path column sums one path per serial batch, chosen by the last worker
to send its finish message. Intervals from concurrent workers overlap. Worker
CPU sums per-process counters across all 206 mutant workers. Worker CPU after
first collection includes test execution and reporting, plus later file
collection in eight workers. CPU from startup through first collection uses the
worker's `started` checkpoint as the split. The corresponding wall interval
starts at controller dispatch.

| Phase | Median per fork wall | Sum along last-finish paths | Sum of mutant worker CPU |
| --- | ---: | ---: | ---: |
| Request to worker fork | 12.90 ms | 0.89 s | — |
| Fork to started | 87.03 ms | 4.78 s | 16.02 s |
| Started to run dispatch | 0.08 ms | <0.01 s | — |
| Run dispatch to first collection | 67.72 ms | 3.73 s | 10.38 s |
| First collection to worker finish | 46.09 ms | 3.78 s | 9.95 s |
| Finish to batch reply / worker stop | — | 0.16 s | 0.17 s |
| Total | — | 13.34 s | 36.52 s |

- The **14.32 s mutation phase** occupied 92% of command wall. Controller
  request-to-reply windows occupied 13.34 s of that phase. The 50 observed
  same-controller gaps totaled **0.72 s**, with a **14 ms** median. The remaining
  phase time includes controller turnover and other untraced scheduler work.
  Lane finish skew was **27 ms** at the median. Skew measures a readiness gap,
  and the scheduler can recover only the portion it can safely overlap.
- The trace saw **8,390 module-fetch RPCs**, about 41 per mutant. The two first
  mutant batches in their controllers had a median **171 ms** dispatch-to-first-
  collection interval on the last-finishing path. The other 50 batches had
  a **69 ms** median. The trace counted requests and did not measure their
  response latency, cache hit rate, bytes, or separate worker evaluation from
  controller work.
- Controller self CPU was **5.10 s** overall, with **3.76 s** inside mutant
  request windows. The mutant forks plus controllers accounted for **41.62 s**
  of the 45.00 s diagnostic process-tree total. Worker stop counters cover
  work through the acknowledgement checkpoint. Post-stop activity, baseline
  forks, Rust, and other work remain in the residual.

## Rust resource accounting

The sampling thread consumed **3.74 s of CPU** in the independent accounting
capture (5.3% of its 70.60 s whole-command CPU). RSS reads used **2.94 s** of that
CPU, process discovery **0.59 s**, and identity reads **0.10 s**. All 537 samples
and their clock reads passed validation. These child stages are included in the
sampling total. The sampler recorded 5.93 s of inclusive wall intervals while
mutant forks could run concurrently, so that figure cannot be treated as
removable command wall time.

## Decision

1. **Separate Node boot from Vitest initialization.** Use a matched fresh
   Node IPC-only worker as a control, then break down the stock worker with
   the run’s compile cache enabled and disabled. Measure startup CPU and wall
   with cold and populated slots. Keep the real worker on the same pinned
   framework and confirm the same process and selector lifecycle. Use those
   results to choose one repeatable source of startup work to remove. Previous
   bundle experiments did not qualify a reliable performance improvement.
2. **Measure where fetch and first collection spend their time.** Time graph
   snapshot, fallback fetch and local module evaluation separately on cold
   and steady batches. Vitest already sends warmed graph entries to fresh
   workers. If it spends appreciable time in misses, test reuse of immutable
   artifacts within the same live controller generation. Check changed
   resident code/maps, selectors, mocks, worker cleanup and 206 exact outcomes
   before scoring. The request count alone is insufficient to select a cache.
3. **Keep sampling and streaming changes secondary.** The observed
   inter-batch gap is small next to the worker phases, and even removing all
   measured sampling CPU in its capture would recover only 5.3% of CPU there.
   Any sampling change must preserve exact admission and cleanup.

The last unprofiled [paired campaign](../2026-11-19-lane-resident-transport/native-campaign.md)
placed colord-scale at four workers at **1.077× Stryker wall, 0.954× CPU and
0.653× peak RSS**. Moving the medians to the targets requires roughly **54%**
less wall and **16%** less CPU from that candidate. The startup interval alone
is too small to close the wall gap. Score any candidate with fresh processes,
matching outcomes, alternating unprofiled pairs and uncertainty intervals on
wall, CPU and peak RSS.

[Per-batch and per-worker data](summary.json) · [Reproduction script](summarize.py)

The raw archives are retained locally under
`/root/oximut-evidence/scale-bottlenecks`. From the repository root:

```sh
python3 benchmarks/results/2026-11-19-main-bottlenecks/summarize.py \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture \
  /root/oximut-evidence/scale-bottlenecks/accounting/artifact/capture \
  benchmarks/results/2026-11-19-main-bottlenecks/summary.json
```
