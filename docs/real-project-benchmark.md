# Real production workload: colord

This benchmark vendors MIT-licensed colord commit
[`1404cfe80cf8fe6df1a876ea3b12aa8eccc7feff`](https://github.com/omgovich/colord/tree/1404cfe80cf8fe6df1a876ea3b12aa8eccc7feff)
(package version 2.10.0). All 41 production TypeScript files and four upstream
test suites plus fixtures are unmodified. The license, original package metadata,
file hashes and adaptation details are in
[the corpus provenance](../benchmarks/real-project/colord/PROVENANCE.md).

The workload declares five mutation source files: `extend.ts` and
`manipulate/{invert,lighten,mix,saturate}.ts`. All 102 upstream tests remain
available, including the scientific reference tests unrelated to those source
files. This is a bounded real-project experiment, not a claim about the complete
colord mutation scope or general JavaScript performance. The plugin tests exercise
registration state and the real import graph. Upstream has no async tests.

## Reproduce

Install from the root lockfile before timing. Required versions are Node 26.3.1,
Vitest 4.1.12, and Stryker core/vitest-runner 10.0.0. The runner checks them.
Use a release Oximut binary and the adapter from the same candidate checkout.
`--adapter` explicitly overrides the runner entrypoint; its parent package is
hashed recursively. Without an override, provide `--adapter-source-dir` for the
binary's matching default adapter package. The binary then keeps its own compiled
default; the caller asserts that the supplied source directory matches it.
For before/after comparisons, pass each candidate's matching directory, never a
shared adapter accidentally taken from the benchmark checkout. Every raw row
records the binary hash, full adapter package file hashes and chosen override.
Sources and binary must remain unchanged during each timed command. These hashes
are provenance of observed files, not proof of how an arbitrary binary was built.

```sh
export PATH=/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin:$PATH
npm ci
# Build separately, outside measurement; serialize builds and benchmarks.
cargo build --release -p oximut-cli
python3 benchmarks/real_project.py \
  --binary target/release/oximut \
  --adapter packages/vitest-adapter/m2-runner.mjs \
  --warmups 1 --repetitions 5 \
  --output benchmarks/results/colord-matched
```

The strict command passes `--mutators arithmetic,boolean-literal` to Oximut.
It requires a binary with that additive CLI filter; candidate 10ef255 does not
provide it. This benchmark branch does not modify the product CLI. To diagnose
that candidate without pretending the family scopes match:

```sh
python3 benchmarks/real_project.py \
  --binary /path/to/current/oximut \
  --adapter-source-dir /matching/checkout/packages/vitest-adapter \
  --diagnostic-unrestricted --warmups 0 --repetitions 1 \
  --output benchmarks/results/colord-diagnostic
```

This diagnostic intentionally uses unrestricted Oximut against explicitly
configured Stryker arithmetic/boolean-literal families and rejects comparative
ratios. A qualification-only ordinary test command is available with
`--tools vitest --warmups 0 --repetitions 1`; its per-run test gate can pass while
the overall cross-tool gate remains false because there is no comparison.

Each run uses a new private copy, forks, one worker, file isolation and disabled
outcome caches. Stryker `vitest.related: false` keeps all upstream tests in its
baseline. Both tools can conservatively select covered tests for mutants.
No test assertion, parser complexity budget, isolation setting or cache validation
is relaxed. This clean-run harness makes no cache invalidation claim; M6's
incremental/cache validation remains unchanged.

The runner serializes tools and alternates order across rounds. `run.py` supplies
process-tree `wait4` CPU accounting and sampled aggregate live `smaps_rollup` RSS.
These are raw process measurements, including startup and tool overhead; memory
is sampled rather than a continuously observed peak. No Tokio/dial9 profiling is
used to measure external Stryker processes.

All rows, report JSON, bounded logs (up to 16MiB, with truncation evidence), input
hashes/configurations, metadata and failures survive. Full private projects remain
on disk for artifact upload, and are excluded from git to avoid repeating the
vendored source. Corpus hashes identify the checked-in source content. The output
directory must not already exist. `measurements.jsonl` retains every raw wall/CPU/
RSS sample result, including unsuccessful runs. Summaries label medians as raw;
failed runs are never turned into successful measurements.

## Exact gates

Ordinary tests must pass and each tool must discover its full pinned list of 102
test identities, including multiplicities. The manifest pins both exact Vitest
name representations (JSON reporter and programmatic coverage); no fuzzy name
matching is used. Source/test/license hashes must agree with provenance.
Oximut report schema, complete baseline/coverage, complete classifications and
Stryker per-mutant test execution are checked through the existing M6 validators.
A known upstream `invert` arithmetic edit must be killed by actual tests.

Stryker's explicitly configured ignored operators remain in its raw report and
are published as scope exclusions with identities and reasons. Active mutants
are never postfiltered to manufacture an overlap benchmark. Edited-source
identity includes the file and exact complete edited content, so whitespace and
parenthesis differences remain mismatches. Timeout, invalid, resource and
infrastructure outcomes invalidate qualification.

A ratio is emitted only when ordinary tests and both mutation runs qualify, the
family declarations and available test scope agree, and the complete exact edited
source sets and classifications agree. Shared subsets only provide diagnostics.
Warmups produce evidence but never ratios. Unit tests exercise corpus tampering,
missing/duplicate tests, incomplete attribution, scope/outcome/family mismatches
and rejection of failed runs.

## Qualification evidence, 2026-10-27

The checked-in `2026-10-27-colord-*` result directories retain initial setup
failures and retries. The first preparation failed because Vitest's generated
local cache was copied; private project preparation now excludes generated
caches. The first diagnostic used the wrong explicit adapter and Stryker's default
related-test baseline omitted two tests. Both were corrected without changing
upstream sources or tests.

The diagnostic retry ran ordinary Vitest (102 passed), Oximut (102 baseline tests;
11 mutants: 10 killed arithmetic, 1 survived comparison-boundary), and Stryker
(102 baseline tests; 10 killed arithmetic, 26 explicitly ignored other operators).
Its original Oximut test-name gate rejected different suite separator formatting.
`revalidation.json` preserves a separate read-only validation after pinning both
exact name representations; the original rows/reports/summary remain untouched.
All three runs then qualify individually, with 10 exact shared mutants and no
classification mismatches. The extra boundary mutant still fails full scope.
No comparative ratio is valid.

Raw retry measurements were Oximut 58.189s wall, 63.572s CPU, 437374976 bytes peak
sampled aggregate RSS; Stryker 21.600s wall, 13.805s CPU, 516128768 bytes RSS.
These are a single diagnostic run on the local host, with different active
workloads, not a speed or memory comparison. A matched filter run and hosted
warmup plus five repetitions remain necessary. Future improvement work should
start from those matched measurements and profile the process showing cost;
this branch changes the experiment, not the execution engine. The parent is
preparing an additive family-filter binary and will run matched qualification
after its coordinated build/test completes. No timed commands were started after
the parent requested a build hold.

## Before/after campaign orchestration

`benchmarks/compare_candidates.py` runs one warmup pair and five measured pairs
by default. Build both binaries **before** invoking it. The baseline is pinned to
`4a6993b564758d15d976cd0998ba490fbf64e1e1`, which has the mutation-family filter
and predates the allocation/invalidation optimization. The candidate includes the
optimization under evaluation; pass its full commit SHA. Both use the same
arithmetic/boolean-literal declaration (the five-file scope has no Boolean
literals), the same 41 available source files and all 102 upstream tests.

The wrapper alternates before/after order each round. Each variant subcampaign
serializes ordinary Vitest, Oximut and Stryker; tool order reverses on alternate
rounds. Warmups use `real_project.py --warmups 1 --repetitions 0`; measured
subcampaigns use `--warmups 0 --repetitions 1`. No installation, compilation,
parallel benchmarks or automatic retries occur in the wrapper. Keep the host free
of other work during the campaign. Each adapter entrypoint is explicitly chosen
from its matching source directory and its entire package hash inventory must
remain identical across every round of that variant. Binary hashes must also stay
fixed. The wrapper records source commit declarations, build flags, tool versions,
binary/adapter hashes, optional build metadata, attempted commands and exit codes.
Local commit/build declarations are supplied by the caller; they are not a
reconstruction of a binary's build provenance.

Every raw subcampaign, failed attempt and partial report is retained. Subcampaign
ratio publication is deferred. No campaign ratio is published unless every
warmup and measured run qualifies individually, each variant is stable across
rounds, and both variants and Stryker have the same complete exact mutation
identities, outcomes and declared test inventory. An invalid run is not discarded
or replaced by a later success. If retrying manually, use a new output directory
and keep the old directory as evidence.

The summary reports paired **Oximut/Stryker** wall, CPU and sampled aggregate RSS
ratios for each variant, plus paired **after/before Oximut** ratios. Pairing uses
the same outer round. Deterministic 10,000-draw bootstrap intervals reuse
`targets.paired_interval`; warmups never contribute ratios. At least one warmup
and five measured pairs are required. Candidate/Stryker targets are wall <= 0.80
and RSS <= 0.50. A target is met only when the interval's upper bound meets the
threshold, missed when its lower bound exceeds it, and otherwise inconclusive.
CPU is reported without an invented target. Before/Stryker results provide
context; after/before measures the change without applying an extra threshold.
Invalid gates fail the command. Missed or inconclusive performance targets remain
published evidence and return success unless `--require-targets` is supplied.
Neither target is promised by the harness or the optimization.

The summary also retains each Oximut report's `resources.phaseWallMs` and
`resources.disabledValidationRuns` as phase evidence, with its round and raw report
path. Fields absent from older binaries remain null; they are never inferred
from wall time or logs. The original report and complete resources remain in raw
subcampaign evidence, including failed campaigns.

For the parent's local comparison after both matching binaries are ready:

```sh
export PATH=/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin:$PATH
python3 benchmarks/compare_candidates.py \
  --before-binary /root/.t3/worktrees/oximut/t3-52f2b967/target/release/oximut \
  --before-adapter-dir /root/.t3/worktrees/oximut/t3-52f2b967/packages/vitest-adapter \
  --after-binary /root/oximut/target/release/oximut \
  --after-adapter-dir /root/oximut/packages/vitest-adapter \
  --after-commit "$(git -C /root/oximut rev-parse HEAD)" \
  --output .oximut/colord-before-after
```

Run from the checkout containing this harness and installed locked dependencies.
Use `real_project.py` directly for a smaller local qualification; the wrapper
requires the full minimum campaign. The command above is documentation only; this
implementation follow-up did not execute benchmarks or builds.

The `Real project before-after benchmark` workflow runs natively on Ubuntu 24.04
x86_64 with a 90-minute job limit, on relevant pull-request paths or manual
dispatch. It creates the baseline worktree in RUNNER_TEMP at the full pinned SHA,
uses a shared CARGO_TARGET_DIR for dependency reuse, and copies the baseline
binary before compiling and copying the candidate. Matching adapters remain in
their respective checkout directories. Both builds and gate tests finish before
timing starts. Runtime paths are set through GITHUB_ENV, not a job-level runner
context. Build metadata records both commits, tool versions, flags, host and
copied binary hashes. The workflow uploads raw projects/reports/logs, all attempts
and summaries even after a failure; dependency symlinks are removed only after
timing to keep artifacts bounded. Hosted runner noise still limits conclusions.

## Local matched qualification, 2026-10-28

[Retained qualification evidence](../benchmarks/results/2026-10-28-colord-local-qualification/README.md)
now includes one matched run for each built candidate. Both passed all exact
workload gates, with 102 tests and the same 10 killed arithmetic edits. The local
sample used less Oximut RSS after the runtime change but more elapsed time and
CPU. It is not a repeated native performance result or target achievement.

[The corrected native synthetic campaign](../benchmarks/results/2026-10-28-matched-native/README.md)
passed its global correctness gate and missed both performance targets at both
scales. It predates the runner optimization. Raw reports, measurements and hashes
are retained alongside the prior invalid campaign. The native colord workflow
must establish its own complete repeated evidence.

A subsequent supervisor candidate also passed the local exact gate. It reduced
raw CPU/wall time relative to the previous local sample, while still trailing
Stryker and missing the targets. Its raw evidence is retained in the same result
directory. The native workflow now evaluates the complete candidate including
that change; single local qualifications are not repeated performance evidence.
