# Zygote workers (Linux)

By default every mutant runs in a fresh Vitest worker process that starts from
scratch: it forks, imports Vitest and runs the tests. On Linux, persistent runs
(`--execution persistent`) use the zygote by default: the
controller instead keeps **one warm Node process** (the zygote) that
has already imported what a worker needs, and `fork()`s each mutant worker from
it. Every mutant still runs in its own process; only the start-up cost goes away.

## Measured effect

On the `colord-scale` workload at 4 workers, the zygote alone against Stryker
measured **0.74x wall time, 0.58x CPU time and 0.65x peak memory**. Stock Oximut
was about 1.0x wall on the same workload. This is one workload on one machine;
see [real-project-benchmark.md](real-project-benchmark.md) for the method and run
the benchmark on your own project before relying on it.

## Requirements

- Linux with glibc (musl/Alpine, macOS and Windows use normal workers).
- A C compiler at install time. The installer builds the addon
  (`packages/vitest-adapter/native/zygote-fork.c`, via
  `scripts/build-zygote-addon.py`) and prints a skip notice when it cannot; the
  install still succeeds. See [install.md](install.md).
- `--execution persistent`, `--isolation fresh-process` (the default) and the
  reference persistent adapter. Other combinations are rejected at start-up.

## Enabling it

It is **on by default** for `--execution persistent` runs on Linux with
`--isolation fresh-process` and the reference persistent adapter. Otherwise
(other OS, fresh execution, `--isolation fresh-thread`, a custom adapter) it is
off silently. If the native addon is missing or fails to load, normal workers
run instead.

```sh
oximut --root . --source src/subject.ts --execution persistent              # auto
oximut --root . --source src/subject.ts --execution persistent --no-zygote  # opt out
oximut --root . --source src/subject.ts --execution persistent --zygote     # require it
```

An explicit `--zygote` errors when its prerequisites do not hold; combining it
with `--no-zygote` is an error.

`OXIMUT_ZYGOTE=1` is the adapter-level switch the CLI sets; you do not need it.

## Fallbacks

Oximut falls back to normal forked workers, with a diagnostic, when:

- the addon is missing or cannot be loaded;
- the zygote dies, or is not ready within 10 seconds.

Outcomes do not change; the run only loses the speed-up. After a zygote failure
every later worker in that run is a normal fork.

## Timeout watchdog

A zygote worker that deadlocks looks like a timeout, which a normal worker would
not produce. So the controller treats a zygote timeout with suspicion:

- A mutant that times out under the zygote is **rerun once on a normal worker**.
  The rerun's result is the one reported.
- If the rerun does **not** time out (a mismatch), the zygote is **disabled for
  the rest of the run**.
- If the rerun times out too, the timeout stands and the zygote stays enabled.
- Costs: a timed-out mutant takes up to two deadlines instead of one, and runs
  without the zygote never rerun timeouts.

## Isolation change

Normally the controller's only children are the workers of running jobs. With a
zygote, one extra declared child, a live direct child of the controller, may
outlive jobs. It is admitted and reaped like any other child. Mutant workers are
forked from it, so each mutant gets its own process and the mutant environment is
set per fork, but they inherit the zygote's warm module state instead of loading
it fresh. Baseline workers remain stock.

## Diagnostics

The report's diagnostics show `zygote=on` or `zygote=off` (with a reason such
as `not Linux`, `--no-zygote` or `isolation fresh-thread`) in the persistent
execution line, and, when the
watchdog acted, `zygote watchdog: N timeout reruns` and, on a mismatch,
`zygote worker timed out where a normal worker did not; zygote disabled for the
rest of the run`. Fallbacks log `zygote workers unavailable` or `zygote workers
disabled` on stderr.

## Precollect (experimental, off by default)

`--zygote-precollect` (with `--zygote` and `--test-selection`) goes one step
further: a mutant worker is forked from a *template* zygote that already fetched,
and for one selected test file also evaluated, the work that worker would repeat.
The mutant is still chosen when the instrumented expression is evaluated
(`process.env.OXIMUT_ACTIVE_MUTANT`), and every mutant still runs in its own
forked process.

A template is built per selected set of test files, on first use, by a hidden
Vitest project, so fetches, resolves and RPC use Vitest's own pool code; its
worker stays up instead of being stopped, and mutant workers are forked from it.
Each child gets its own IPC channel and runs a second Vitest `start` and `run`.

- **One selected file:** the template evaluated the file's modules with no mutant
  active. The child runs with `isolate: false`, so only the test file and its
  setup files are evaluated again; every other module is inherited copy-on-write.
- **Several selected files:** each file must start from fresh modules, so the
  template only leaves fetched code and loaded externals behind; modules are
  evaluated in the child as usual.

Mutants evaluated while a module loads would never see their mutation in an
inherited module, so they keep the ordinary zygote path:

- the instrumenter classifies each mutant (`loadTime`): true unless the mutated
  expression is inside a function body that runs only when called. Immediately
  invoked functions, functions passed as arguments, class field initializers,
  static blocks and computed keys are load time; unknown is load time. The rule
  is syntactic and cannot see a top-level call into a function declared elsewhere;
- selection must be `files`. A mutant the baseline probes saw evaluated outside
  any test (module load, collection, including through calls from other modules)
  already selects the full suite, so a mutant on this path was never load-time
  in the baseline;
- test or setup files using `vi.mock`, `vi.hoisted`, fake timers, `vi.stubEnv`
  and similar load-time Vitest APIs keep the ordinary path.

Known deviations: a test file's top-level code runs once in the template and
again in the child, and templates (one per selected file set) stay resident,
which costs memory. The diagnostics report how many mutants were eligible.

## Status

Experimental: the flag, its prerequisites and its diagnostics may change. CI
(`.github/workflows/zygote.yml`, not a required check) runs it on Ubuntu 22.04
and 24.04, proves outcome parity against a normal run, and proves the Alpine
fallback.
