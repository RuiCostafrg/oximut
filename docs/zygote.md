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

`--zygote-precollect` goes one step further. It needs `--zygote` and
`--test-selection` (selection tells it which test file a mutant runs), and the CLI
refuses to start without them. A mutant worker is forked from a *template* zygote
that already fetched, and for one selected test file also evaluated, the work that
worker would repeat.
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

Top-level code also runs once in the template, so whatever it leaves behind is
shared by every concurrent fork: a directory from `mkdtemp`, an open server or
socket, a child process, a timer, a request in flight. A quiescence guard
(`zygote/quiescence.cjs`) watches the template's collect only and makes the
template **ineligible** when collect

- left active handles or requests beyond those it started with
  (`process.getActiveResourcesInfo()` before and after),
- left a timer pending (including unref'd ones; timeouts that fired and cleared
  intervals do not count),
- wrote, created or removed files or directories (`fs` and `fs.promises` write,
  append, `mkdir`, `mkdtemp`, `rename`, `copyFile`, `cp`, `rm`, `unlink`, links,
  `createWriteStream`, `open` for writing), or
- started a child process (`spawn`, `exec`, `execFile`, `fork` and their sync forms).

An ineligible template is dropped and its mutants take the ordinary zygote path,
which is still correct, only slower. The run reports it as
`zygote precollect: N templates ineligible (active handles: ..., fs writes: ...)`
and the timeline trace has a `template_ineligible` event with the details.
`OXIMUT_ZYGOTE_PRECOLLECT_GUARD=off` disables the guard, for measuring its cost.

Known deviations: a test file's top-level code runs once in the template and
again in the child, and templates (one per selected file set) stay resident,
which costs memory. The diagnostics report how many mutants were eligible.

## Rolling lanes (experimental, off by default)

With the shared controller, mutants normally run as batches of `width` lanes: a
batch ends when its slowest lane has finished, and every batch is one round trip
between the Rust scheduler and the controller. `--rolling-lanes` removes both costs
for fork-based lanes (it needs the shared controller and works with or without
`--zygote`):

- The scheduler sends **one large batch**: up to 32 lanes per slot, bounded by the
  8 MiB request size and by lanes that share the very same resident module set (one
  allocation, which is what lets the batch carry it once). The batch is priced as it
  travels: the resident set once, and each lane without it, its mutation reduced to
  a reference (empty `code`, null `map`) to the resident module of its file, which
  the slot and the adapter resolve (lane encoding `run-shared-lane-resident-v2`; an
  older controller still gets the expanded request). The controller keeps
  `width` lanes busy through it. Each slot owns a stock Vitest pool in the shared
  context, so as soon as a slot's worker has exited and its result is attributed,
  the next queued mutant starts there. Nothing waits for the slowest lane.
  A batch of at most `width` lanes, a memory-limited batch and thread isolation
  keep the barrier behaviour; a Vitest build whose pool factory cannot be found
  runs the lanes in barrier rounds.
- Every mutant still runs in its own forked process, results are attributed per
  lane, and a resident or image change still drains every slot first, because a
  batch only holds lanes of one resident set.
- **Deadlines stay per lane.** A lane's timer starts when its slot takes it (after
  any wait for its template), not when the batch arrives. The batch's own deadline,
  which the scheduler and the coordinator enforce, is one lane deadline plus its
  1 s reporting time, times the number of rounds `ceil(lanes / width)`: it is an
  upper bound on a healthy batch and cannot fire before the last lane could finish.
  A controller that hangs is therefore noticed after that bound, not after one lane
  deadline.
- Unhandled errors carry no project. One that arrives while lanes run taints the
  lanes running at that moment, as the whole batch was tainted before; a pool error
  explained by that lane's own deadline or crash taints nobody.
- With `--zygote-precollect`, every template the batch needs is started when the
  batch arrives, in the order its lanes need them, and builds one after another
  while the first lanes already run. A lane whose template is still building waits
  for it; its deadline has not started. Templates are built one at a time (the
  hidden project runs one collect), and a template asked for twice is built once.
- **Templates start before the batch.** The CLI knows each mutant's selected test
  files as soon as the instrumented baseline is done. It then sends the controller
  that will run the batch (slot 0, which ran the baselines) a `warm` request with
  `templates`: the most common sets of test files, at most one per lane slot. The
  controller answers at once, creates the lane context and starts those templates
  in the background; the batch waits only for the context. Whatever does not come
  up is simply not a template, and a prebuild for sets the batch does not use costs
  only those templates. The window is the CLI's own planning (cache lookups, input
  checks) plus the batch's dispatch; template builds themselves (about 0.6 to 0.9 s
  each on the matched-scale fixture, one at a time) are longer than that, so the
  first lanes still wait for their templates. `OXIMUT_TEMPLATE_PREBUILD=off`
  restores the old order, for comparing the two. The timeline analyzer's
  `templates:` line shows how many templates were ready before the first mutant
  dispatch and how far ahead of it they were started.
- The zygote watchdog is unchanged: a lane that times out under the zygote is
  rerun once on its own with the zygote bypassed.

The timeline trace keeps its per-lane events. In rolling mode the batch-oriented
metrics (pool_run, lane tail skew, result to reply) do not apply; the analyzer
reports a rolling summary with the lane job time and the slot refill gap, and the
lane workers alive over time. The scheduler's `batch_built` event records why each
batch stopped growing (`capacity`, `bytes`, `queue_empty` or `resident_mismatch`).

## Status

Experimental: the flag, its prerequisites and its diagnostics may change. CI
(`.github/workflows/zygote.yml`, not a required check) runs it on Ubuntu 22.04
and 24.04, proves outcome parity against a normal run, and proves the Alpine
fallback.
