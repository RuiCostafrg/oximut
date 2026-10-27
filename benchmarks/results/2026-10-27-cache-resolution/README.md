# Cache resolution — Linux arm64, 2026-10-27

The default policy is now automatic: use complete outcome validation when its
measured cost is worthwhile, otherwise execute mutations freshly. Instrumentation
caching remains enabled. Explicit `always` still performs complete validation.
No baseline or declared/inherited input is dropped to obtain these results.

Five measured repetitions (wall-time medians):

| Fixture / mode | Prime | Warm | Forced |
| --- | ---: | ---: | ---: |
| M1 before, legacy explicit reuse | 18.72 s | 20.38 s | 6.82 s |
| M1 after, `always` | 13.84 s | 13.27 s | 5.96 s |
| M1 after, `auto` | 14.86 s | 6.05 s | 6.15 s |
| Pure after, `auto` | 27.24 s | 13.69 s | 17.25 s |

Explicit warm validation is about 35% faster, but remains uneconomical for M1.
Auto bypasses outcomes on M1, matching fresh-work cost, and retains all 18 hits on
Pure, about 21% faster than its forced reference. Prime includes cold policy
calibration and is slower than forced. These are warm-run observations, not an
amortized guarantee including cold runs or the eight-bypass periodic reprobe.
`medians.json` retains all individual timing values; raw CSV/JSONL, reports,
comparison gates, metadata, output logs, and advisory histories are retained.

## Protocol and provenance

One external warmup pair was discarded, followed by five measured before/after
pairs with alternating binary order. Each one-shot harness runs prime, unchanged
warm, then forced. Its internal `warmup` field is false even in the discarded
`warmup-before`/`warmup-after` directories: exclude these two entire directories
when calculating the paired result. Auto campaigns use the harness's ordinary
one warmup and five repetitions, alternating warm/forced order. Projects and
policy history start fresh for each repetition. `campaign.py` records exact
commands and order; binary/toolchain paths must be adjusted when reproducing.
The helper resolves the containing checkout from its own location.
All binaries/builds/acceptance tests finished before these timings; no concurrent
builds or test suites ran during the campaign.

Before is release build of `70fcc877bece422b923a5bbec39f399db5e1a2f2`:
`46e5aa27d95cfe3648da0ce4d1070b1702668ac75e8daaf38f245e457ec0d769`.
After is a regular `cargo build --release --locked -p oximut-cli` from that base
plus `implementation.patch`, with binary SHA-256
`da8b8079548959913012594236ddfc9bd97f3779812d8c10686e642f09b108e8`.
The patch includes the benchmark/test changes as well as all binary source and
lockfile changes. Documentation and the later CLI contract tests do not alter
that binary. No C assembler flags or extra assembler dependency was required.

Metadata's `git_revision` is the base commit with a dirty worktree; its source
hashes describe the current checkout on **both** sides, not the old binary's
sources. Before provenance is the base revision and binary digest above.
The generic metadata profile label says `user-supplied; default debug`; both
supplied binaries here are release builds. Raw absolute report paths point to
the original `.oximut/cache-resolution` locations; reports are copied under the
same relative paths here. Host is the pinned Linux arm64 Ubuntu/Termux PRoot
environment (Node 26.3.1, Rust 1.96.0, Python 3.13, Vitest 4.1.12); metadata records
full platform/tool versions and input hashes.

For a new build and output directory, reproduce auto measurements with:

```sh
cargo build --release --locked -p oximut-cli
python3 benchmarks/m6.py --binary target/release/oximut --fixtures m1 pure \
  --tools oximut --scenarios incremental --cache-policy auto --changes \
  --repetitions 5 --warmups 1 --output .oximut/cache-auto-reproduction
python3 benchmarks/m6.py --binary target/release/oximut --fixtures m1 \
  --tools oximut --scenarios incremental --cache-policy always \
  --changes source test helper snapshot config lockfile environment \
  --repetitions 1 --warmups 0 --output .oximut/cache-input-reproduction
```

To reproduce the paired campaign, build the base revision in a separate checkout,
retain its binary, then build the candidate and adjust the two binary paths and
output directory in `campaign.py`. Old binaries require `legacy`, which omits the
new CLI flag; candidate explicit reuse uses `always`.

## Correctness and limits

Every subcampaign passes cache/forced classification agreement. Explicit reuse
requires all unchanged hits; all seven input edits require invalidation and fresh
agreement. Auto bypass is recorded separately as fresh-work agreement, never as
proof of invalidation. `gates.json` indexes every harness correctness gate.

Validation logs retain 46 passing M1–M5/installer acceptance tests on this exact
release binary (copied to the conventional `target/debug/oximut` test path), 36
Rust tests, Clippy, and 42 benchmark unit tests. Digest known answers include a
forced software backend; four fixtures' list output (3/18/4/12 mutants) remained
identical. Three focused CLI contract checks and five report-validator tests also pass;
all 128 archived CLI reports validate against the published schema. These
later checks are retained in separate logs.

Accurate resource accounting was part of the final candidate: `smaps_rollup`
replaced approximate stat RSS where available. The retained same-PID diagnostic
shows why: stat reported about 31 MiB while rollup reported about 117 MiB for a
resident 64 MiB allocation. That diagnostic deliberately failed an interim guard;
it is investigation evidence, superseded by the final passing acceptance log.
The final fixture touches pages and retains strict admission/calibration checks.
External benchmark RSS still uses approximate stat, so differing accounting and
stale external samples preclude a precise memory-saving claim. Keep all forced
rows visible rather than attributing reference variation solely to hashing.

These fixture/PRoot measurements establish neither native-host support nor a
cross-tool speed claim. The historical M6 scope mismatch with Stryker and the
real-project/scaling/distribution release gates remain open. See the
[cache decision](../../../docs/cache-performance.md) and
[1.0.0 roadmap](../../../docs/release-roadmap.md).

## Reproduction helper corrections after review

PR #13 review corrected reproduction-helper setup issues after measurement:
`campaign.py` now resolves its containing checkout and creates missing output
parents while still rejecting an existing campaign directory; the
software-backend manifest declares its own
workspace so it runs directly inside this checkout. The originally measured
helper sources remain available in commit `1b8b8bb`. Commands, raw measurements,
reports, and original validation logs are unchanged. `SHA256SUMS` tracks the
current checked-in helper files and this explanation.

To rerun the archived software-backend known answers without adding build
artifacts to this archive:

```sh
RUSTFLAGS='--cfg sha2_backend="soft"' cargo run --locked \
  --manifest-path benchmarks/results/2026-10-27-cache-resolution/validation/software-backend/Cargo.toml \
  --target-dir /tmp/oximut-software-backend-target
```
