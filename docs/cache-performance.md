# Cache performance and release policy

Outcome caching remains opt-in through `--cache --cache-inputs-complete`.
The default `--cache-policy auto` measures whether outcome validation is worth
its cost. It selects fresh mutation execution when complete fingerprinting costs
at least 125% of fresh mutation work. This resolves the small-workload cache
regression without allowing advisory history to authorize cached results.
`--cache-policy always` retains explicit complete validation and reuse.

The implementation upgrades SHA-256 to a version with runtime CPU acceleration
and a software fallback. Digests and mutant IDs remain unchanged. For a nonempty
all-hit inventory, it combines the post-baseline and pre-publication scan after
worker cleanup. No mutation jobs run between these checks. If inputs changed,
it discards candidates and retries once with fresh source generation, baselines,
mutation execution, and separate validation scans. Read errors, failed cleanup,
baseline failures, and resource exhaustion fail closed. See the full
[M4 contract](m4.md) and [report reference](report-schema.md).

Automatic policy history is bounded, checked, and advisory. Missing or corrupt
history requires fresh calibration; history can only choose to do fresh work.
Successful bypasses update the fresh-work estimate, with a full recalibration
after eight bypasses and hysteresis when fresh work becomes expensive enough.
Source, options (including report destination), runner, or environment identity
changes can reset learning. Calibration and periodic probes cost more than a
forced run; this policy does not promise every invocation will be faster.

The regression campaign also exposed stale `/proc/PID/stat` RSS: one live worker
reported about 31 MiB there while its `smaps_rollup` reported about 117 MiB.
Resource accounting now prefers rollup `Rss`, with checked conversion and an
approximate stat fallback only where rollup is unavailable/restricted. Malformed
live-process accounting fails closed. Admission remains sampled, not a hard
cap. This changes measurement accuracy and has a cost; see [M5](m5.md).

## Evidence and remaining release gates

Five measured repetitions per case produced these wall-time medians:

| Fixture and mode | Calibration/prime | Warm | Forced |
| --- | ---: | ---: | ---: |
| M1, previous explicit reuse | 18.72 s | 20.38 s | 6.82 s |
| M1, updated explicit reuse | 13.84 s | 13.27 s | 5.96 s |
| M1, updated automatic policy | 14.86 s | 6.05 s | 6.15 s |
| Pure, updated automatic policy | 27.24 s | 13.69 s | 17.25 s |

Explicit warm validation improved about 35% on M1, but still costs more than
fresh execution there. Automatic policy therefore bypasses outcomes on M1,
while retaining all 18 cached outcomes on Pure, where warm execution is about
21% faster than the corresponding forced reference. Prime/calibration is
included separately: these are warm-run comparisons, not a claim that the first
run or the average including periodic calibration beats forced execution.

The [raw cache campaign](../benchmarks/results/2026-10-27-cache-resolution/README.md)
records five measured repetitions, discarded warmups, paired forced execution,
changed-input checks, binary provenance, classification agreement, and test logs.
Its scope is the pinned Linux arm64/Termux PRoot environment and deterministic
fixtures. It establishes the observed cache policy behavior on these workloads;
it does not establish broad native Linux, real-project, or Stryker speed claims.
External benchmark RSS still uses approximate stat counters, so this campaign
must not be used to claim a precise memory reduction.

The cache implementation passes 46 local acceptance checks plus three focused
CLI contract checks, 36 Rust tests, 42 benchmark tests, and five schema-validator
tests; all 128 archived reports validate. The acceptance corpus includes
input races, cache corruption, failed teardown, resource exhaustion, cancellation,
and process isolation. Report/list versioning and focused CLI checks start the
next contract work. Before 1.0.0, finish that contract audit, run the exact release
candidate and final distribution on every supported host, verify installation
and provenance, and publish the support/update policy. Those remaining gates
are tracked in the [release roadmap](release-roadmap.md).
