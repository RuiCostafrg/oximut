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
