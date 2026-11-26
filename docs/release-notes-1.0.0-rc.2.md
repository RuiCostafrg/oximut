# 1.0.0-rc.2 release notes (unpublished draft)

rc.2 is a release candidate for **Linux x86_64** (validated on Ubuntu 24.04), not
stable 1.0.0. It carries everything merged to `main` since rc.1 (tag
`v1.0.0-rc.1`, 2026-11-04): a much faster default execution path, new defaults,
and one change to how mutants are classified. It is distributed as the curated
source archive plus, for the private Homebrew tap, a validated prebuilt prefix;
see [distribution](distribution.md). Nothing here is published to npm or
crates.io, and the prebuilt prefix is not attested (only the source archive and
its metadata are).

## What you must know before upgrading

### New defaults (and how to opt out)

Persistent execution, test selection and the fast modes are now on without flags.

| Mode | rc.2 default | Opt out |
| --- | --- | --- |
| Execution | `--execution persistent` (one reused Vitest controller; every mutant still runs in a fresh process) | `--execution fresh` (the reference mode) |
| Test selection | on | `--no-test-selection` |
| Zygote fork server | auto on Linux/glibc when persistent, `--isolation fresh-process`, reference adapter | `--no-zygote` |
| Zygote precollect | auto when zygote, test selection and the shared controller are on | `--no-zygote-precollect` |
| Rolling lanes | auto with the shared controller | `--no-rolling-lanes` |

- *Auto* means a missing prerequisite silently skips the mode. The run prints one
  stderr line naming each resolved mode (`oximut: modes: execution=persistent
  (default), ... zygote=on (auto) ...`) and the reason an auto mode is off; the
  same appears in the report diagnostics.
- The shared controller, precollect and rolling lanes need two or more workers.
  In rc.2 the default is still `--workers 1`, where only persistent execution, test
  selection and the zygote apply; on `main` after rc.2 the default is
  `--workers auto` (StrykerJS's rule on the usable CPUs, see
  [M5](m5.md#worker-count)). The benchmark below uses 4 workers.
- An explicit positive flag (`--zygote`, `--zygote-precollect`, `--rolling-lanes`)
  errors when its prerequisites do not hold. `--x` together with `--no-x` is an error.
- The outcome-cache fingerprint covers the resolved modes, so results cached by
  rc.1 or under other modes are never reused.
- **Custom adapters default to fresh.** A `--adapter` other than the installed
  reference adapter keeps the fresh (M1) protocol unless you pass
  `--execution persistent`, so existing adapters run unchanged. The zygote,
  precollect and the shared controller apply only to the reference persistent
  adapter. The installed launcher picks the persistent adapter unless
  `--execution fresh` is given.
- `--execution fresh --no-zygote --no-test-selection` is the reference
  configuration CI compares the fast modes against.

### Mutants that break test collection are now `killed`

A mutant that makes a test file throw while Vitest collects it, or makes a suite
hook throw, previously ended as `infrastructure-error` and made the run
incomplete (exit 2). It is now `killed` with the error kept as evidence, because
the code under test failed. Faults of the tool itself (unhandled errors,
interruption, process exits, IPC loss) remain `infrastructure-error`, and a
baseline with such an error is still a failed baseline. Mutation scores can rise
and runs that used to be incomplete can now complete; see
[report-schema.md](report-schema.md).

### Linux only, glibc for the zygote

The zygote fork server is a native Node-API addon
(`packages/vitest-adapter/native/zygote-fork.c`, compiled at install time by
`scripts/build-zygote-addon.py`). It needs Linux with glibc and a C compiler at
build time. The validated prefix already contains the addon built on the
Ubuntu 24.04 validation host; it needs glibc 2.34 or newer, and the Oximut
binary itself needs glibc 2.39. On musl/Alpine, macOS, a source install without
a compiler, or an addon that fails to load, Oximut falls back to normal workers
with the same outcomes and only loses the speed-up (`zygote=off`). See
[Zygote workers](zygote.md).

## Changes since rc.1

- **Execution engine:** a native Rust coordinator drives the Vitest controller;
  parallel mutants share one controller by default; Vitest 5.1.0; stop a
  mutant's job at its first failing test; per-run Node compile cache; shared
  module transforms and resident modules across lanes; event-driven
  scheduler-to-slot hand-off; cheaper resource sampling.
- **Zygote** (Linux): mutant workers are forked from one warm Node process
  instead of starting from scratch, with a timeout watchdog that reruns a
  zygote timeout on a normal worker and disables the zygote on a mismatch.
  **Precollect** forks from a template that already collected the mutant's one
  test file and falls back to the ordinary zygote path when the template is not
  quiescent. **Rolling lanes** keep every lane busy instead of waiting for the
  slowest lane of a batch, and zygote lane workers exit at their result so
  slots refill immediately.
- **Install:** the installer compiles the optional zygote addon into the bundle
  and never fails the install when it cannot; `scripts/smoke-install.py
  --require-zygote` fails unless the addon is present, the report says
  `zygote=on` and mutant workers were actually forked from the zygote.
- **Reports:** additive schema-1 fields `sources` (per-file SHA-256 and mutant
  counts) and `mutators` (machine-readable mutator set).
- **Verification:** an outcome-parity CI corpus runs the fast modes against the
  reference configuration on pinned real open-source Vitest projects and fails on
  any per-mutant difference ([benchmarks/parity](../benchmarks/parity/README.md)).

## Performance evidence (and its limits)

Matched comparison against Stryker 10.1.1 on the vendored `colord-scale`
workload (206 mutants), 4 workers/concurrency 4, 5 measured pairs after a
warm-up, `rc.2` defaults, and identical mutant outcomes to the fresh reference.
Ratios are Oximut divided by Stryker (lower is better); medians.

| Host | Wall | CPU | Peak RSS | Run |
| --- | --- | --- | --- | --- |
| Intel Xeon 6975P-C (GitHub-hosted) | **0.405x** | 0.367x | 0.909x | [run 45606491157](https://github.com/RuiCostafrg/oximut/actions/runs/45606491157) (`main` at `15824e1`) |
| AMD EPYC 7763 (GitHub-hosted) | **0.468x** | 0.440x | 0.899x | [run 45593119113](https://github.com/RuiCostafrg/oximut/actions/runs/45593119113) (PR #91, `7eebd03`) |

For reference, the Xeon run's "before" candidate (`main` at `7297840`, after #63)
measured 1.26x Stryker's wall time on the same workload, so rc.2 is about 3.1x
faster than that earlier main in wall time (after/before 0.319x wall, 0.381x CPU).
Its peak memory is 1.18x that earlier main's, because warm zygote and template
processes stay resident.

Honest limits:

- One workload (a vendored `colord` corpus), one worker count (4), GitHub-hosted
  shared runners, Linux x86_64 only. There is no macOS or Windows data and no
  evidence for other project shapes, worker counts or test suites; run it on your
  own project before relying on any ratio.
- The memory result is a 9-10% reduction against Stryker on these runs. It does
  **not** meet the project's internal 20% memory goal, and the benchmark harness
  reports its targets as not all met for that reason.
- The AMD run is from a PR branch (before it merged), not a release commit.
  Neither run measured the release commit itself; the release-commit evidence is
  the correctness gate below, not a speed claim.
- Installation, build and the correctness gate run outside any measured
  benchmark. Outcome identity is established per workload and by the parity
  corpus, not proven in general.

## Release gate

The exact-artifact gate ([R3](distribution.md)) builds the source archive,
installs it once on Ubuntu 24.04 x86_64, and smokes the installed and the
relocated prefix in fresh and persistent modes, including cold, warm, forced and
invalidation cases, with `--require-zygote`. A manually requested candidate can
additionally carry source provenance and an SPDX 2.3 inventory, as in rc.1; see
[source provenance](source-provenance.md), [notices](../NOTICE.md) and the
[rc.1 notes](release-notes-1.0.0-rc.1.md) for the unchanged licensing scope.
