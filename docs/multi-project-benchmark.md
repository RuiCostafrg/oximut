# Multi-project benchmark: does the colord result generalize?

Every earlier speed claim comes from one vendored workload (colord, [real-project-benchmark.md](real-project-benchmark.md)).
`.github/workflows/multi-project-benchmark.yml` runs Oximut with its **default flags** against the
newest StrykerJS (`@stryker-mutator/core` and `@stryker-mutator/vitest-runner` as pinned in
`package.json`, `coverageAnalysis: perTest`) on several pinned real projects at matched worker counts.
Results are evidence, never a gate. Triggers: manual dispatch (inputs: `projects`, `workers`, `rounds`,
`large_rounds`, `warmups`, `scope`; defaults run every project at 2 and 4 workers, 5 measured rounds for the small
projects and 3 for the large ones, after 1 warmup), and pull requests
touching the harness, which run a one-round smoke (ufo and es-toolkit-no-globalthis, 2 workers). The label
`multi-project-full` on such a pull request runs the same full matrix. GitHub only dispatches a
workflow that exists on the default branch, so a branch that adds or changes the workflow uses the
pull-request triggers.

## Projects

| Kind | Projects | Why |
|---|---|---|
| `startup` | the outcome-parity corpus, `benchmarks/parity/corpus.json`: ufo, pathe, hookable, std-env, tslog (3 to 57 Oximut mutants) | tiny scopes: wall time is dominated by fixed start-up cost |
| `large` | es-toolkit (array, math, string, object, predicate and util modules plus the lodash-compatible math, string and util ones; 398 mutants), pinned in `benchmarks/multi-project/projects.json`; es-toolkit also runs as `es-toolkit-no-globalthis`, the same scope minus the two sources that mention `globalThis`, because Oximut does not probe such files and that turns test selection off for the whole run | hundreds of mutants: wall time is dominated by per-mutant cost |

Every project is a pinned commit (exact SHA, fetched shallow; nothing is installed) run against the
repository's Vitest, with a benchmark-owned Vitest config in node environment and the isolation Oximut
requires (`benchmarks/parity/configs/`, `benchmarks/multi-project/configs/`). The two groups are reported
in separate tables and never averaged together. Mutant counts come from `--count-mutants` (`oximut --list`).

Scopes are chosen so the plain suite runs in well under a minute in one fork, node-only, with only Vitest
installed: Oximut's per-job deadline is 30 s by default and its baseline must fit it. Specs needing a DOM
environment, packages the repository does not install or external tools are left out of the test list, and
sources they alone cover are left out of the mutation scope.

Candidates tried first, which Oximut with default flags could not complete (kept here as evidence; none is in
the corpus):

* valibot (`library/src`, 467 mutants): its schemas are built at module load, so about 190 mutants select the
  whole 3237-test suite; one round at 2 workers did not finish in 30 minutes (307 mutants done).
* radix3 (`src`, 688 mutants): the suite's longest file pushed Oximut's instrumented baseline past 30 s; with
  that file removed, 61 of the first 177 mutants (mostly parser loops) ran into the 30 s job deadline and a round
  did not finish in 30 minutes; with the parser-like files removed, the baseline failed with `controller message
  exceeds the response limit`.
* es-toolkit with the lodash-compatible array, object and predicate modules: about a minute of suite, so
  Oximut's baseline timed out.

## Matched scope and what stays different

Both tools mutate exactly the same source files (the harness resolves the globs once and passes the
same list to Oximut `--source` and Stryker `mutate`). They do not generate the same mutants:

* Oximut has three families: arithmetic, boolean-literal, comparison-boundary. In the default `matched`
  scope, Stryker runs only the operators that correspond (`ArithmeticOperator`, `BooleanLiteral`,
  `EqualityOperator`); every other Stryker operator is excluded. `--scope full` runs everything each tool
  has by default (Stryker then generates many times more mutants).
* The operators still differ in detail (Stryker's equality operator also flips `===`, its boolean operator
  also drops `!`, it reprints some expressions), so every cell records both exact edited-file identity
  sets (`benchmarks/m6.py` `mutation_identity`), how many edits are shared, and whether the tools agree
  on the shared mutants. Unlike the colord harness, unequal scopes are reported, not rejected.
* `vitest.related` is switched off for Stryker, as in the colord benchmark; pass `--stryker-related` to keep
  Stryker's default.

## Cells and measurement

One cell is one project at one worker count: Stryker `concurrency` = Oximut `--workers`. Each round runs
plain Vitest (suite time, for diagnosis), Oximut and Stryker sequentially in clean private copies with no
cache, alternating the order, using the same process-tree measurement as the other campaigns
(`benchmarks/run.py`: wall, CPU from `wait4` rusage, peak aggregate RSS). A cell fails (and records why)
when a run times out, a command fails, the Oximut baseline fails, Oximut reports engine failures
(infrastructure error, resource exhaustion) or ran fewer workers than requested. Oximut timeouts and
invalid mutants keep the run (they are real behavior) but flag it incomplete, and so do memory-pressure pauses of
Oximut's admission (its default memory budget; the colord harness rejects them).

Worker counts above the usable CPUs are recorded as `skipped`, never run: Oximut caps its workers at the
CPU count and Stryker does not, so they would not be matched. GitHub standard runners have 4 vCPUs, so
2 and 4 workers run there and 8 is skipped. Each cell records the runner CPU model, since GitHub assigns
different CPUs to different cells; compare ratios within a row, not wall times across rows.

## Results table

`benchmarks/multi_project_report.py` (the workflow's summary job) writes one table per kind with: project,
mutants (Oximut / Stryker), workers, CPU model, medians of Oximut and Stryker wall, wall ratio with a 95%
interval, CPU ratio, RSS ratio, the Oximut outcome sha (`mutation_identity_outcome_sha256`, as in the
parity corpus), and killed / survived / no-coverage counts of both tools. Ratios are Oximut divided by
Stryker, paired per round; the interval is the deterministic paired percentile bootstrap used by
`benchmarks/targets.py` (about the min-max range at 3 rounds). Further tables show where the time goes
(Vitest suite time, share of Oximut's wall in its mutation phase, how many Oximut mutants selected the
whole suite or timed out, wall per mutant for both tools, Stryker's initial run) and the mutant overlap.

Flags call out cells worth a second look: Oximut slower than Stryker, a wall ratio above the 0.5x goal,
a detection-rate gap of 10 percentage points or more between the tools, shared mutants classified
differently, one tool generating twice as many mutants, outcomes that differ between rounds, and an
incomplete Oximut run.

```sh
cargo build --release -p oximut-cli
python3 scripts/build-zygote-addon.py
python3 benchmarks/multi_project.py --count-mutants
python3 benchmarks/multi_project.py --projects ufo --workers 4 --rounds 3 --output /tmp/cell
python3 benchmarks/multi_project_report.py /tmp/cell
python3 benchmarks/tests/test_multi_project.py
```
