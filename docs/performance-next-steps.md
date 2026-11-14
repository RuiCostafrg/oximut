# Performance follow-up after W11

The goal remains elapsed time at most 0.50 times Stryker, and total CPU time
and peak aggregate RSS at most 0.80 times Stryker, on equivalent workloads.
Keep fresh-process isolation as the default. Report uncertainty and correctness
before claiming a target; results on colord alone do not establish general gains.

## Current evidence

W11's [five-pair campaigns](../benchmarks/results/2026-11-14-batch-accounting/README.md)
improve colord-scale elapsed time versus #66 at two and four workers. The small
workload is consistent with no change. Modest CPU/RSS regressions remain possible
within the intervals; strict noninferiority was not established.

The larger workload remains slower than Stryker and misses the CPU goal at both
worker counts. Only the four-worker scale RSS result meets the memory goal.
The small four-worker workload meets CPU/RSS goals but misses the elapsed goal.
W11 is an incremental improvement, not completion of the project objective.

## Agent responsibilities

- GPT Sol coordinates implementation, correctness review, evidence and PRs.
- GPT agents implement isolated experiments and independently review changes.
- OpenCode research recovers prior evidence and checks proposed work against
  rejected experiments. Source claims must be checked against the current path.
- Resume unfinished work; do not restart merged tasks. Keep this parent task
  unsettled while child work remains. A finished turn is not a completed goal.

## Next implementation gates

1. Finish W11's PR checks and address actionable review findings. Preserve raw
   measurements, the remaining local peak-capture failure and sampling caveats.
2. Qualify a Vitest 5.1.0 bundled-worker experiment before product integration.
   PR44 is a Vitest 4.1.12 structural probe, not a compatible backend. Vitest 5
   removed the old external runner boundary; redesign must retain collector
   identity, import resolution, module metadata and dynamic-import behavior.
   First prove fresh distinct process/realm identities and stock/bundled outcome
   equivalence, mocks/hooks/module state, maps, deadlines and descendant cleanup.
   Record exact dependency and source provenance. Fail unsupported cases clearly.
3. Only a qualified prototype proceeds to opt-in product integration and scored,
   unprofiled before/after campaigns: small and scale workloads at two and four
   workers, one warmup and five measured pairs, equivalent mutants, build-integrity
   and correctness gates, whole-tree CPU and peak RSS. Reject unsupported claims
   or resource tradeoffs that miss the owner's goals.

## Deferred approaches

Directly seeding PR62's lane cache with baseline results is unsupported. The
baseline and lane plugins/configs differ, and closing baseline deletes temporary
artifacts. A safe handoff would need audited pipeline equivalence, generation-
aware capture, independently owned bounded artifacts and consumer-drain cleanup.
It could save only remaining cold transforms and adds copying/hashing costs;
do not weaken fingerprints or retain a live baseline merely to obtain hits.

Pre-spawned workers (PR45), baseline overlap (PR59) and context warm-up (PR61)
already have negative resource or elapsed evidence. Do not repeat them without
a distinct mechanism and a testable reason the result should change. Warm reused
workers remain unproven because previous probes found state contamination.
