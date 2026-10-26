# M6 fixture evaluation — 2026-10-26, Linux arm64 / PRoot

This is a source-evaluation campaign, not evidence for a public alpha performance
claim. The checked-in fixtures run on the pinned Linux Node 26.3.1/npm 11.16.0,
Vitest 4.1.12, Stryker 10.0.0, and Rust 1.96.0 stack. Each measurement directory
contains its own exact environment, revision, dirty-state and implementation
hashes. The source changes were uncommitted during measurement; the binary hash
and input hashes identify what was tested. Hardware/affinity/container observations
are in `metadata.json`, including background load and virtualized host limits.

The commands run from the repository root. On this host the pinned Linux Node
was selected explicitly; the inherited Android executable is unsuitable:

```sh
export PATH=/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin:$PATH
cargo build --release --locked -p oximut-cli
python3 benchmarks/m6.py --binary target/release/oximut \
  --fixtures pure stateful --scenarios clean --repetitions 5 --warmups 1 \
  --output benchmarks/results/2026-10-26-m6-arm64/clean
```

Use a new output directory to repeat a campaign. Build/install time is excluded;
measured processes start cold, while OS filesystem caches remain untouched.
The harness runs one invocation at a time, reversing fixture/scenario order on
alternate rounds. Warmups are retained but excluded from medians. CPU is process
tree `wait4` rusage; memory is sampled aggregate RSS at 20 ms, with shared pages
counted per process. The separate runner admission samples use 50 ms. Neither
measurement installs a hard cap, and short peaks can be missed.

## Validation evidence

`validation/acceptance.txt` records all 33 M1–M5 acceptance checks passing,
including cache-input invalidation, worker admission/recycling, exhaustion,
sibling cleanup, slot death, and SIGTERM. `validation/rust-tests.txt` and
`validation/clippy.txt` retain Rust verification. The lifecycle experiment and
npm audit passed (zero reported vulnerabilities at check time).

The initial adapter suite ran concurrently with acceptance/build work: 18 of 19
tests passed; the CPU-hang cleanup test exceeded the supervisor deadline. Its
isolated rerun passed, verifying both timeout classification and detached-child
cleanup. Both outputs are retained, not silently replaced by the successful run.
Formatting, linting, and TypeScript checks passed with the pinned Linux runtime.
The initial inherited Android runtime could not load Biome and was corrected
before these checks. Performance timings were started after builds/tests stopped.

`install-validation.json` records source installation and relocation smoke
validation. Fresh and persistent executions from an external project each killed
one mutant; a Node import probe verified the installed adapter entrypoints.
The install was moved to a path containing spaces and overwrite refusal was
checked. `package-name.json` records the npm registry name lookup; it is not a
reservation. The repository is MIT licensed and remains unpublished.

## Scope and decision

Cross-tool mutation identity is exact edited-file content and relative path.
The harness publishes the entire overlap and exclusions; explicitly disabled
Stryker operators are counted separately from tested mutants. Equal outcome
classifications on a shared subset do not authorize full-run speed ratios.
Oximut full-run reports expose baseline counts rather than test identities; their
inventory check is limited to expected fixture counts. Identity stability is
checked where ordinary Vitest and Stryker expose inventories.

Application-like here means the small checked-in stateful fixture. It does not
mean a representative real application. This campaign does not establish large
project scaling, platform portability, strict memory caps, or the proposed 2×
speed/30% memory targets against equivalent-scope Stryker. Continue source
validation; hold public alpha performance claims pending the broader protocol
and a matched mutation scope. See [the M6 decision](../../../docs/m6.md).

## Clean measurements
All 60 invocations passed: one warmup and five measured repetitions for each of
five scenarios on each fixture. Every optimized/reference and shared cross-tool
classification comparison passed. Medians below retain min/max in `clean/summary.json`.

**Pure fixture:**
- oximut clean-fresh: 46.53 s wall (45.96–61.44), 50.97 s CPU, 240.3 MiB peak RSS.
- oximut clean-persistent: 16.86 s wall (16.11–29.48), 17.38 s CPU, 273.4 MiB peak RSS.
- oximut clean-selection: 17.91 s wall (17.29–30.95), 18.26 s CPU, 281.1 MiB peak RSS.
- stryker clean-stryker: 38.93 s wall (35.18–47.34), 27.95 s CPU, 372.0 MiB peak RSS.
- vitest ordinary: 2.06 s wall (1.53–3.18), 1.63 s CPU, 143.8 MiB peak RSS.

**Stateful fixture:**
- oximut clean-fresh: 15.47 s wall (14.97–15.63), 16.73 s CPU, 237.9 MiB peak RSS.
- oximut clean-persistent: 9.03 s wall (8.55–9.48), 9.56 s CPU, 250.2 MiB peak RSS.
- oximut clean-selection: 8.23 s wall (7.94–8.53), 8.47 s CPU, 241.3 MiB peak RSS.
- stryker clean-stryker: 23.19 s wall (21.32–25.88), 14.97 s CPU, 370.9 MiB peak RSS.
- vitest ordinary: 2.86 s wall (1.79–3.32), 2.07 s CPU, 144.7 MiB peak RSS.

These are descriptive timings of different workloads, not cross-tool speed
comparisons. Pure has 18 Oximut mutants and 33 comparable Stryker mutants, with
16 exact edits shared. Stateful has 4 Oximut mutants and 5 comparable Stryker
mutants, with 4 exact edits shared. Explicitly excluded Stryker operators and
the remaining edit differences are retained in raw reports and comparisons.

Within Oximut, fresh/persistent median wall ratios are 2.76× (pure) and 1.71×
(stateful). Persistent median peak RSS is about 14% and 5% higher respectively,
so controller reuse is an elapsed-time improvement here, not a memory reduction.
Fresh/selection ratios and all denominators are in the machine-readable summary;
small fixture differences must not be generalized to real applications.

## Initial incremental campaign rejected

The `incremental/` directory is a **failed diagnostic campaign**, excluded from
performance conclusions. Its harness used a different JSON report destination
and report-related environment value for every role. Oximut fingerprints those
inputs, so an ostensibly unchanged run correctly invalidated its cache. The gate
caught zero reused outcomes. A concurrent correctness-only smoke during the tail
of that invalid campaign validated the fix; none of these timings are accepted.

The corrected harness keeps the live report destination and benchmark environment
stable across a prime/incremental/forced group, removes stale output before each
invocation, and copies each fresh report into its permanent archive afterward.
A regression test checks stable arguments/environment and distinct archived
reports. The original harness is retained in `initial-harness.py`; its hash matches
metadata for `clean/` and the rejected `incremental/` campaign. To reproduce that
original code, restore the snapshot at `benchmarks/m6.py` in a disposable checkout.
Clean scenarios are unaffected by the cache-only destination fix.

## Accepted unchanged incremental measurements
`incremental-stable/` passed all gates: one warmup and five measured repetitions
of each tool's prime, warm, and forced runs. Primes are retained separately and
excluded from comparison medians. Every Oximut warm run reused all three
outcomes after baseline validation; forced runs reused none. Stryker's logs
reported reuse of four comparable outcomes. These are different mutation scopes.

- oximut forced: 6.12 s wall (5.67–12.03), 6.12 s CPU, 239.4 MiB peak RSS.
- oximut incremental: 22.37 s wall (20.88–25.19), 14.69 s CPU, 235.6 MiB peak RSS.
- stryker forced: 20.37 s wall (17.08–23.11), 12.56 s CPU, 348.6 MiB peak RSS.
- stryker incremental: 19.13 s wall (14.43–21.38), 11.35 s CPU, 349.9 MiB peak RSS.

Outcome reuse is correct, but **Oximut caching regresses elapsed time on this
small fixture**: 22.37 s warm versus 6.12 s forced. The complete cache-enabled
path costs more than the mutation work it saves. Individual hashing/baseline
phases are not separately timed, so the next design task is to profile that
path while preserving the conservative input fingerprint and fresh baseline.
No incremental performance win or cross-tool ratio is claimed.

```sh
python3 benchmarks/m6.py --binary target/release/oximut \
  --fixtures m1 --tools oximut stryker --scenarios incremental --changes \
  --repetitions 5 --warmups 1 \
  --output benchmarks/results/2026-10-26-m6-arm64/incremental-stable
```

## Changed-input correctness commands

```sh
python3 benchmarks/m6.py --binary target/release/oximut \
  --fixtures m1 --tools oximut stryker --scenarios incremental \
  --changes source test helper snapshot config lockfile environment \
  --repetitions 1 --warmups 0 \
  --output benchmarks/results/2026-10-26-m6-arm64/changed-inputs
```

These single-repeat runs are correctness probes, not performance estimates.
Source/test/config edits add comments, the imported helper changes an unused
export, the snapshot is an inert input, the lockfile changes metadata, and the
declared environment value changes without changing fixture behavior. Oximut
requires a persisted eligible prime, invalidates all outcomes, and agrees with a
forced run. Stryker's native reuse is reported; forced agreement on these benign
edits does not prove that Stryker implements the same invalidation policy.


All 48 changed-input invocations passed their gates. Every one of the seven
Oximut edited inputs invalidated all three outcomes and agreed with forced
execution; the unchanged control reused all three. Stryker pairs also agreed
with forced execution, with native reuse reported separately.

All 110 archived Oximut reports validate against the published JSON Schema;
see `validation/report-schema.json`. The archive includes accepted and rejected
campaigns, so schema conformance must not be confused with a passed performance
or cache gate. Current harness regression coverage is 40 passing tests.
