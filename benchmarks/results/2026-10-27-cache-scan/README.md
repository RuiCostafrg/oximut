# Cache traversal release-build comparison

This follow-up tests a deliberately narrow refactor: precompute the lexical
cache-directory exclusion and combine `.git`/Vite component checks. Every full
input read, hash contribution, symlink policy, and validation pass is preserved.
The source patch is included. It is **not evidence of an end-to-end speedup**.

## Builds and commands

The before binary was built from commit `3a4a9a4` with
`cargo build --release -p oximut-cli --locked`, then copied to
`/tmp/oximut-cache-before`. The after binary was built with the same command plus
`source-change.patch`, then copied to `/tmp/oximut-cache-after`. Each campaign
records its binary SHA-256 and runtime/host metadata. The harness metadata's
worktree source hashes were captured after the edit for both runs; use the
commit and patch above for the distinct binary source provenance.

Using the pinned Linux Node, run for SIDE `before` then `after`:

```sh
python3 benchmarks/m6.py --binary /tmp/oximut-cache-SIDE --fixtures m1 --tools oximut --scenarios incremental --changes --repetitions 3 --warmups 1 --output .oximut/cache-scan-SIDE
```

The campaigns ran sequentially, one warmup plus three measured repetitions each,
on the same host. Builds/tests/installations were idle during timing. Agent
read/edit work on CI validation files continued; those files are outside the
fixture and adapter fingerprint inputs. Timing order was not randomized across
binaries, and these small-fixture measurements cannot establish causality or
representative release performance. Within each campaign the harness alternates
incremental/forced execution order. Filesystem caches were left untouched.

## Results

| Median wall seconds | Before | After |
| --- | ---: | ---: |
| Prime | 17.77 | 20.21 |
| Unchanged incremental | 20.07 | 22.39 |
| Forced | 5.75 | 7.39 |

Both campaigns passed every correctness gate and reused all three outcomes on
warm runs. Forced timings moved too, so the difference cannot be attributed to
the refactor. Keep the lower-allocation traversal, but claim no speed benefit.
The dominant full-input work remains; do not remove validation passes or rely on
mtime-only memoization to make the benchmark faster. `comparison.json` retains
samples, fingerprint input times, byte counts, and gates.

A separate correctness-only campaign ran:

```sh
python3 benchmarks/m6.py --binary /tmp/oximut-cache-after --fixtures m1 --tools oximut --scenarios incremental --changes source --repetitions 1 --warmups 0 --output .oximut/cache-scan-invalidation
```

Its unchanged and edited-source gates passed. The source edit invalidated all
three outcomes and agreed with forced execution. Schema validator tests ran
during this separate campaign, so its timings are not performance evidence.

## Validation and release status

- Ten CLI cache tests passed, including 186 differential exclusion-path cases.
- Cargo formatting and CLI/all-targets Clippy with warnings denied passed.
- Independent review found no exclusion-policy change.
- Five report-validator contract tests passed; all three existing actual M6 CI
  smoke reports validated. The CI workflow passed actionlint.
- The new validator also checked this archive; see `schema-validation.txt`.

The complete M1–M5 acceptance suite was not rerun for this narrow refactor.
Full release-candidate correctness and failure testing remain required. Outcome
caching still costs more than forced execution here; the R2 release policy is
open, and no public release or performance claim is authorized by these results.
