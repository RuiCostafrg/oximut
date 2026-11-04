# Whole mutant controller capture (diagnostic only)

Native Ubuntu 24.04 x86_64 [run 41839060730](https://github.com/RuiCostafrg/oximut/actions/runs/41839060730)
captures PR #38's merge revision `69481738decebabd34a854cb73bf6ddc6d25b2bb`.
The source archive, full project copies, binary and logs are available only in
that run's workflow artifact, with the workflow's configured **14-day retention**
window; they may be deleted earlier and are not durably archived in this repo.
After expiry, committed hashes identify those original files but cannot recover
them or independently revalidate the full build and workload capture by themselves.
The committed profile, timeline and extracted resources remain available for
the narrower controller-capture revalidation described below.
This capsule retains unmodified controller timeline, CPU profile, original
diagnostic summary and build metadata; `SHA256SUMS` covers those files and the
extracted resource evidence. The latter records the original measurements hash
and revalidation with the strengthened validator from the Sourcery review fixes.
Both workload qualification and the stronger capture gate pass: 102 tests,
ten identical killed arithmetic edits, 14 controller commands, 18 observed forks
and exits, one complete first-mutant profile. No automatic retry was performed;
the duplicate queued label/open-event workflow was cancelled before capture.

## Resources (profiler overhead, one run, no ratios or CIs)

| Tool | Wall s | CPU s (user / sys) | Peak aggregate RSS bytes | Peak / observed processes |
|---|---:|---:|---:|---:|
| Oximut | 3.485372 | 4.798962 (3.874800 / 0.924162) | 344846336 | 4 / 21 |
| Stryker | 3.099526 | 4.316967 (3.960359 / 0.356608) | 578162688 | 4 / 4 |
| plain Vitest | 0.922962 | 1.195662 (1.017372 / 0.178290) | 283717632 | 2 / 5 |

These values must not be compared as a scored performance campaign: the
controller alone was profiled, there is one sample and no paired bootstrap CI.
Existing qualified ratios are retained in the [baseline capsule](../2026-11-04-edgy-baseline/evidence.json).

`resources.phaseWallMs`: inputAndSchedulerPreparation 1.854514;
originalBaseline 918.351660; instrumentedBaseline 650.503575;
postBaselineValidation 0.000305; mutationPlanning 0.058415;
mutationExecution 1883.348749; teardown 26.216231;
finalInputValidation 0.000314. No phase gate was relaxed.

## Interpretation

Across ten mutant requests, the median request/reply duration is 147.865 ms
(143.296–266.273). Independently computed boundary medians are:

| Interval | Median ms | Range ms |
|---|---:|---:|
| Request to fork | 3.280 | 2.723–68.353 |
| Fork to started response | 74.222 | 69.663–76.501 |
| Run dispatch to collection RPC | 23.242 | 21.899–91.857 |
| Collection to testfileFinished | 46.195 | 44.244–60.146 |
| testfileFinished to controller reply | 1.025 | 0.641–10.854 |

These are observed wall intervals, not a CPU partition, and their medians are
not additive. Collection/completion includes Vitest/RPC/reporting work, not just
assertions. Fork startup is material; there is no evidence of a mandatory
245 ms orchestration floor independent of it. This host/run is faster than the
earlier scored hosts, so do not subtract its timings from their medians.

The first mutant costs 266.273 ms. Inspector startup precedes the fork by about
65 ms and accounts for about 64 ms of sampled `node:inspector` time. About
143 ms of samples are idle. Controller transform/AST parsing, stack/source-map
processing and GC also appear. This first mutant additionally crosses the
coverage-to-resident graph invalidation boundary. It is not a typical warmed
job. The second mutant also has higher controller CPU; later jobs generally
spend only about 10–36 ms of CPU in the controller process. Worker CPU remains unprofiled.

At the first mutant boundaries, controller RSS changes from 207585280 to
226009088 bytes and used heap from 40943176 to 45098136 bytes, including profiler
allocations. At controller exit RSS is 245751808 and used heap 35001240 bytes.
This single trajectory does not establish a leak, a memory saving, or where
aggregate peak RSS is retained; RSS and used V8 heap do not measure the same thing.

The next highest-leverage experiment remains an opt-in warm worker runtime with
a demonstrably fresh realm and dependency/module graph. Reusing external module
state is insufficient. A later worker CPU capture could distinguish parsing
from module initialization within the measured startup interval. Batching spec
arrays without changing that boundary has no demonstrated large benefit.
