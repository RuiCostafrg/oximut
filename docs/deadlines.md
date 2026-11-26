# Job deadlines

Every job (a baseline run, a mutant run) has a deadline; a job still running when it
passes is stopped, its process tree reaped, and reported as `timeout`. A deadline that is
the same for every project fails in both directions: a large suite's baseline takes
minutes, a mutant that is evaluated at module load legitimately runs the whole suite, and
a mutant that loops forever in a tiny test file should not hold a worker for as long as
either. Oximut therefore derives each job's deadline from how long the baseline says that
job's tests take, the way StrykerJS does (`netTime * timeoutFactor + timeoutMS`, with a
separate dry-run timeout).

## Baselines

The original and the instrumented baseline each get `--baseline-timeout-ms`, **600000 ms
(ten minutes)** by default. The bound is only there to stop a hung suite; a slow one
should finish. With `--timeout-ms` and no `--baseline-timeout-ms`, the baseline gets the
`--timeout-ms` value, as it always did.

## Mutants

```text
mutant deadline = baseline time of the test files the mutant runs * --timeout-factor
                  + --timeout-constant-ms
```

* The baseline reports how long each test file took in its worker (import, environment
  and test setup, collection and the tests themselves; `baseline.fileTimings` in the
  report). The slowest of the baseline runs counts for each file, since they run with
  parallel workers and probes.
* A mutant runs the test files that selection chose for it, one after another in one
  fresh worker, so its time is the **sum** of those files' times. A mutant that selects
  every test (a module-load mutant, an incomplete coverage collection, `--no-test-selection`)
  gets the sum over the whole suite.
* `--timeout-factor` (default **1.5**, at least 1) leaves room for variance and for
  parallel baseline files running slower than a lone mutant.
* `--timeout-constant-ms` (default **10000**) covers what the baseline does not measure:
  forking the worker, collecting the test files and, for the first job of a cold shared
  controller, its start-up (job deadlines include it). It is also the floor: an
  infinite-loop mutant in a small test file is stopped after about ten seconds, not the
  30 that a fixed deadline used to cost.
* A mutant that runs as a **rolling lane** of the shared controller (the default with two
  or more workers) needs less: its clock starts when its worker is asked to run, after
  the controller is warm, the template ready and the worker forked. Such a job gets a
  constant of **5000 ms** (what StrykerJS adds) instead. On a traced colord-scale run
  with four workers the part of a lane's run that is not the tests was 3 ms until
  collection began, 7 ms (p90 15 ms) to collect and 12 ms (p90 34 ms) from the last test
  to the result. A lane that has to run on an ordinary worker anyway (a zygote watchdog
  rerun, or a fallback when the shared controller fails) gets the 5000 ms back, so a rerun's deadline is the
  full one. An explicit `--timeout-constant-ms` is used for every job and an explicit
  `--timeout-ms` is never derived. The report's `deadlineMs` is the deadline the first
  attempt ran under.
* The result is bounded by 86400000 ms (24 h).

An adapter that reports no per-file times (a custom adapter) gets the baseline job's wall
time, spread over the baseline's workers, as the whole-suite time, and a selection of some
files takes its share of the tests. Files the adapter did not time are treated the same way.

## `--timeout-ms`: an explicit deadline

`--timeout-ms N` keeps its meaning: **one deadline of N ms for every job**, baselines and
mutants, with no derivation. Use it to reproduce a fixed policy or to make timeouts fast in
tests. `--baseline-timeout-ms` can still give the baseline its own.

## Lanes, rolling lanes and the zygote watchdog

* The deadline is part of each mutant's request, so every path that runs the job uses it:
  a one-job request, a lane of a barrier batch and a lane of a rolling batch.
* Lanes of one batch may have different deadlines. A lane has its own from the moment it
  starts (the start of the batch in a barrier batch), and the adapter stops its worker when
  that passes. The deadline of the batch itself is a bound that cannot fire while a lane
  could still run: for each round of `width` lanes in order, that round's longest lane
  deadline plus one second to report, summed.
* A mutant that times out under `--zygote` is rerun once on a normal worker, unless its
  worker was seen burning CPU or reported Vitest's own timeout (see [zygote.md](zygote.md)).
  The rerun is the same request with `bypassZygote` set, so it has the same scaled deadline.
* Controller start-up is charged to the first jobs of a cold controller as before, which is
  why the constant of a job that is not a lane is not small.

## Evidence

Each executed mutant records the deadline it ran under in `deadlineMs`
([report-schema.md](report-schema.md)). A `timeout` outcome with a deadline well above the
selected files' baseline time means the mutant really did not finish; one close to the
constant means the files were fast and the mutant looped.

A `timeout` is a detection, as in StrykerJS: it counts in the score's numerator
and denominator (`summary.detected = killed + timeout`), does not make the run
incomplete, and exits like a kill. It stays its own outcome so you can see it, and
outcome caching never reuses it because it depends on the machine's speed.
