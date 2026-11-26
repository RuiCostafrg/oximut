# 1.0.0-rc.3 release notes (unpublished draft)

rc.3 is a release candidate for **Linux x86_64** (validated on Ubuntu 24.04), not
stable 1.0.0. It carries everything merged to `main` since rc.2 (tag
`v1.0.0-rc.2`, `63c0845`, 2026-11-25): seven changes, of which one changes a
default (`--workers`) and one changes what a timeout means. It is distributed the
same way as rc.2: the curated source archive plus, for the private Homebrew tap,
a validated prebuilt prefix; see [distribution](distribution.md). Nothing here is
published to npm or crates.io, and the prebuilt prefix is not attested (only the
source archive and its metadata are).

## What you must know before upgrading

### `--workers` now defaults to `auto` (more parallelism and memory)

Until rc.2 the default was `--workers 1`, so the shared controller, zygote
precollect and rolling lanes (which need two or more workers) were off unless you
asked for workers. rc.3 resolves the default, and the new spelling `--workers
auto`, like this ([M5](m5.md#worker-count), #100):

- **Rule:** StrykerJS's default concurrency applied to the CPUs this process may
  use, counting the scheduler affinity mask and every visible cgroup v1/v2 CPU
  quota, not the host's core count: N CPUs give N workers up to 4, and N-1 above
  4 (1, 2, 3, 4, 4, 5, 6, 7 for 1 to 8 CPUs; 15 for 16). At least 1, at most 1024.
- **Memory cap:** an automatic count is also held to 512 MiB per worker of the
  memory budget (the smaller of physical memory, any cgroup limit and
  `--memory-budget-mib`, minus the reserve). A 2 vCPU / 4 GB box gets 2 workers;
  16 CPUs on 2 GiB get 3. Memory admission still measures the real per-worker cost
  at run time and can lower `effectiveWorkers`.
- **`--workers N` is an explicit override** and is never lowered. **`--workers 1`
  restores the rc.2 behavior** (and keeps the multi-lane modes off, as before).
- **Cost:** by default Oximut now uses more CPUs and more memory at once than in
  rc.2. Projects whose tests share ports, files or databases must pass
  `--workers 1` (see [compatibility](compatibility.md)).
- The resolved count is printed in the stderr modes line (`workers=7 (auto: 8
  CPUs)`, `workers=3 (auto: 16 CPUs, limited by 2048 MiB of memory)`, `workers=2
  (explicit)`) and in the report diagnostics; `resources.configuredWorkers` is the
  resolved count.
- **The outcome cache misses once.** The cache fingerprint hashes the resolved
  worker count (killed/survived outcomes can depend on concurrency), so a cache
  written under the old implicit `--workers 1` is not reused after upgrading.
  `--workers auto` and `--workers 4` that resolve to the same number share a cache.

### Timeouts are derived from the baseline (what `timeout` means has changed)

The fixed 30 s deadline for every job is gone as a default
([deadlines](deadlines.md), #96):

```text
mutant deadline = baseline time of the test files the mutant runs * --timeout-factor
                  + --timeout-constant-ms
```

- The reference adapter reports the baseline time per test file; a mutant gets the
  sum over the files selection chose for it, and a mutant that runs the whole
  suite (module-load mutants, incomplete coverage, `--no-test-selection`) gets the
  sum over the suite. A custom adapter without per-file times falls back to the
  baseline wall time shared out by test count.
- New flags: `--timeout-factor` (default **1.5**), `--timeout-constant-ms`
  (default **10000**), `--baseline-timeout-ms` (default **600000**, ten minutes,
  for the original and the instrumented baseline). Bound: 24 h.
- **`--timeout-ms N` keeps its meaning:** one fixed deadline of N ms for every job,
  no derivation. Use it to reproduce the rc.2 policy exactly.
- Mutants that run as rolling lanes of the shared controller (the default with two
  or more workers) get a **5 s** constant instead of 10 s, because their clock
  starts when their worker is asked to run; reruns on an ordinary worker get the
  full 10 s back (#98).
- Why: with the old fixed 30 s, large suites failed their baseline outright and
  mutants that legitimately run the whole suite were reported `timeout` (7 in
  es-toolkit, 61 of 177 in radix3 in the multi-project work), where StrykerJS
  reports them killed or survived. A mutant that loops forever in a small test
  file now holds a worker for about its derived deadline (around 5 to 10 s here)
  instead of 30 s. Verdicts for mutants that really hang are unchanged.
- **Reports gain fields** (additive, schema 1): `deadlineMs` on executed mutants
  (the deadline the first attempt ran under) and `baseline.fileTimings`
  (`{file, durationMs}` per test file). See [report-schema.md](report-schema.md).

### Everything else in short

- Baseline coverage that does not fit the adapter's 1 MiB response limit is sent as
  index runs instead of explicit test lists, and only dropped (every mutant then
  runs the whole suite, reason `coverage-payload-too-large`) above roughly 5000
  tests. Before, exceeding the cap failed the whole baseline. Selection planning,
  which was quadratic in the tests per mutant, is fast again (#96).
- Apart from the additive report fields above, the cache, report and exit-code contracts are unchanged from rc.2.

## Changes since rc.2

- **Deadlines derived from baseline timing** (#96): see above, plus lanes carrying
  their own deadlines (enforced per lane in barrier and rolling batches) and the
  zygote watchdog rerun keeping the scaled deadline.
- **Per-file probe fallback** (#95): a file that merely *reads* `globalThis` is
  now probed like any other. Only a file that redefines or assigns `globalThis`,
  binds it, uses `with`, or names the reserved hook (`__oximut_m3_hit__`) is
  refused, and only **that file's** mutants run the full suite (selection reason
  `unprobed-file: <file>: <reason>`, with a diagnostic). Before, one such file
  turned test selection off for the whole run (es-toolkit: every mutant ran all
  1,913 tests). See [M3](m3.md).
- **Selection with setup files** (#99): `setupFiles` no longer disable selection
  run-wide. Only mutants hit while a setup file loads (no current test) run the
  full suite (`outside-test-execution`); mutants only tests reach keep narrow
  selection. `global-setup`, concurrent/retried/shuffled tests and unsupported
  contexts still fall back run-wide, as documented in [M3](m3.md).
- **Memory admission** (#97): admission samples Pss (proportional set size, falls
  back to Rss) so pages shared by the zygote and templates are counted once, and
  prices a shared controller per lane and a baseline controller per fork instead
  of charging a whole controller tree to one job. This fixes 2 to 4 worker
  scaling, where admission used to run the budget out after one job.
- **Genuine-hang discriminator** (#98): under `--zygote`, a timed-out mutant is
  only rerun on a normal worker when its worker could have been deadlocked. A
  worker seen burning CPU (at least 20% of a core before the deadline) or one that
  reported Vitest's own timeout keeps its `timeout` verdict without the watchdog
  rerun; idle or unknown workers are still rerun, including the disable-zygote-on-
  mismatch rule. A lane stopped at its deadline now kills its whole process tree.
  A genuine infinite loop used to pay its deadline twice.
- **Automatic workers** (#100): see above.
- **Multi-project benchmark workflow** (#94): `benchmarks/multi_project.py`,
  `benchmarks/multi_project_report.py` and
  [multi-project-benchmark.md](multi-project-benchmark.md) compare Oximut (default
  flags) with StrykerJS on pinned real projects at matched worker counts. This is
  evidence tooling; it changes nothing in the product.

## Performance evidence (and its limits)

Two runs on `main` at `d774e1b` (every change in these notes merged; the
version bump in this release commit changes no code), both dispatched by the
maintainer on GitHub-hosted 4 vCPU runners. Oximut ran with its **default flags**
except `--workers`, which the harness passes explicitly so that Oximut and
Stryker run at matched concurrency (Stryker 10.1.1, `vitest-runner`,
`coverageAnalysis: perTest`). Ratios are Oximut divided by Stryker (lower is
better), paired per round, median, with the 95% bootstrap interval for wall.

### Multi-project run: [45759264600](https://github.com/RuiCostafrg/oximut/actions/runs/45759264600)

Both tools mutate exactly the same source files, with Stryker limited to the
operators that match Oximut's three families (arithmetic, boolean literal,
comparison boundary). **They still generate different mutants: Stryker generated
1.7x to 3.7x as many mutants as Oximut in this matched scope** (2.4x to 3.7x on
every project except ufo, where it is 97 against 57; see the mutant-count column), because Stryker has more operators per family (for example `===`
flips, dropping `!`, reprinted expressions) and Oximut has only three mutator
families. Wall, CPU and memory are therefore *per run of each tool's own mutant
set*, not per mutant, and detection rates are not comparable (see the limits).
Small projects (a 3 to 57 mutant scope; start-up cost dominates) and large
projects (about 400 mutants; per-mutant cost dominates) are different regimes.

| Project | Workers | Mutants Oximut / Stryker | Wall ratio [95% CI] | CPU ratio | RSS ratio |
| --- | --- | --- | --- | --- | --- |
| hookable | 2 | 12 / 44 | 0.253x [0.230, 0.261] | 0.228x | 0.759x |
| hookable | 4 | 12 / 44 | 0.261x [0.240, 0.264] | 0.180x | 0.603x |
| pathe | 2 | 38 / 91 | 0.367x [0.351, 0.371] | 0.348x | 0.900x |
| pathe | 4 | 38 / 91 | 0.374x [0.368, 0.381] | 0.355x | 1.114x |
| std-env | 2 | 3 / 8 | 0.551x [0.544, 0.702] | 0.569x | 0.894x |
| std-env | 4 | 3 / 8 | 0.555x [0.547, 0.827] | 0.588x | 0.913x |
| tslog | 2 | 50 / 174 | 0.209x [0.204, 0.220] | 0.206x | 1.185x |
| tslog | 4 | 50 / 174 | 0.231x [0.225, 0.235] | 0.202x | 1.032x |
| ufo | 2 | 57 / 97 | 0.417x [0.413, 0.426] | 0.401x | 1.977x |
| ufo | 4 | 57 / 97 | 0.421x [0.407, 0.432] | 0.335x | 1.303x |
| es-toolkit | 2 | 398 / 966 | 0.423x [0.417, 0.425] | 0.389x | 0.906x |
| es-toolkit | 4 | 398 / 966 | 0.468x [0.465, 0.470] | 0.432x | 0.739x |
| es-toolkit (no `globalThis` files) | 2 | 396 / 956 | 0.418x [0.414, 0.424] | 0.383x | 0.866x |
| es-toolkit (no `globalThis` files) | 4 | 396 / 956 | 0.451x [0.446, 0.452] | 0.412x | 0.625x |

Five paired rounds for the small projects, three for es-toolkit, one warm-up
each. Reading it honestly:

- Oximut's wall time is 0.21x to 0.55x of Stryker's on every cell and its CPU time
  0.18x to 0.59x, but **part of that is Oximut running fewer mutants** (see the
  mutant counts), not only running each one faster. On the large projects the
  per-mutant wall cost is similar: es-toolkit 222 / 279 ms per mutant (2 / 4
  workers) for Oximut against 217 / 245 ms for Stryker, so the lead there comes
  almost entirely from the smaller mutant set. At 4 workers Oximut spent *more*
  wall per mutant than Stryker on es-toolkit.
- **std-env misses the project's 0.5x wall target** (0.551x and 0.555x, with an
  upper bound of 0.83x at 4 workers) on a 3-mutant scope where two of the three
  mutants run the whole suite (selection fell back, `outside-test-execution`).
- **Memory is not uniformly lower.** Peak RSS ratios range from 0.60x to 1.98x:
  Oximut used more memory than Stryker on ufo (1.98x / 1.30x), tslog at 2 workers
  (1.19x) and pathe at 4 workers (1.11x). The project's 20% memory goal (RSS ratio
  at most 0.8x) is met on four of the 14 cells (hookable at both counts and the
  two es-toolkit scopes at 4 workers).
- **es-toolkit runs are flagged incomplete** because 7 mutants end `timeout`
  (genuine infinite loops; the same 7 in every round and cell). Stryker reports
  17 to 20 timeouts on the same es-toolkit scopes. 1 to 2 of the roughly 390 shared mutants
  are classified differently by the two tools.
- Each cell ran on its own GitHub runner and GitHub assigned different CPU models
  (AMD EPYC 7763 and 9V74, Intel Xeon 6975P-C, 6973P-C and Platinum 8573C), so
  compare the ratios, which are paired on one runner, and not walls across rows.

### Real-project run: [45759270273](https://github.com/RuiCostafrg/oximut/actions/runs/45759270273)

The vendored `colord-scale` workload (206 mutants in both tools; the harness
rejects unequal scopes), 4 workers, 5 measured pairs after a warm-up, Intel Xeon
Platinum 8573C.

| Comparison | Wall | CPU | Peak RSS |
| --- | --- | --- | --- |
| rc.3 candidate `d774e1b` / Stryker | **0.442x** [0.437, 0.454] | 0.403x | 0.926x |
| older main `7297840` ("before", after #63) / Stryker, same run | 1.306x | 0.972x | 0.764x |
| `d774e1b` / `7297840` | 0.339x | 0.415x | 1.209x |

The harness reports its targets as not all met: wall (0.5x) and CPU (0.8x) are
met, and **peak memory is not** (0.926x against a 0.8x goal). The rc.2 notes
recorded 0.405x wall for this workload on a different CPU (Xeon 6975P-C); this run
is on a slower Xeon model, so the two numbers are not a regression or an
improvement of each other. The "before" rows are the older main the harness compares
against (`7297840`, after #63), not the rc.2 tag; they are context, not a claim.

### Limits

- **Linux x86_64 only**, GitHub-hosted shared runners with **4 vCPUs**: worker
  counts above 4 were not measured (8 is skipped by the harness), and there is no
  macOS, Windows, other CPU or larger-machine evidence.
- **Matched operator scope, not matched mutants.** Oximut has **three mutator
  families**; Stryker's matched subset generates 1.7x to 3.7x as many mutants. The
  harness records the overlap (for example es-toolkit: 390 identical edits, 8
  Oximut-only, 576 Stryker-only). The tools' detection rates (Oximut 50 to 100%,
  Stryker 71 to 94%) are computed over different mutants and must not be
  compared as mutation scores. Both flag score gaps of 10 pp or more.
- Six pinned projects (five tiny, and es-toolkit at two scopes) are a small
  sample; none is your project. Run it on your own before relying on a ratio.
- One 3-round run per large cell; each cell on its own runner and CPU model.
- Neither run measured the release commit itself (`d774e1b` plus a version bump
  and docs); the release-commit evidence is the correctness gate below.
- Installation, build and the correctness gate run outside any measured
  benchmark. Outcome identity is established per workload and by the parity
  corpus, not proven in general.

## Release gate

The exact-artifact gate ([R3](distribution.md)) builds the source archive,
installs it once on Ubuntu 24.04 x86_64, and smokes the installed and the
relocated prefix in fresh and persistent modes with `--require-zygote`. New in
rc.3: the installed smoke pins `--workers 1` for its cold, warm, forced and
invalidation cases (as of #100, so those cases stay comparable) and adds one
persistent run **without** `--workers`. That run must report the automatic
default (`workers=N (auto: ...)`), complete, kill a mutant and, with
`--require-zygote`, fork its mutant workers from the zygote, so the default users
get is exercised on the installed and the relocated prefix. A manually requested
candidate can additionally carry source provenance and an SPDX 2.3 inventory; see
[source provenance](source-provenance.md), [notices](../NOTICE.md) and the
[rc.1 notes](release-notes-1.0.0-rc.1.md) for the unchanged licensing scope, and
the [rc.2 notes](release-notes-1.0.0-rc.2.md) for the defaults introduced in rc.2.
