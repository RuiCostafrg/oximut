# Vitest 5.1 worker bundle diagnostic

This experiment bundles the worker and the public test API into shared ESM
chunks with separate entry facades. A fork-only resolver hook redirects the
stock Vitest URLs to those facades. The normal controller and installed
package files stay intact. That gives test imports and the worker one
collector and mock runtime, while each mutant still gets a fresh process.

Two native Ubuntu x86-64 runs compared one stock capture and one bundled
capture each, at four workers. Every capture passed the historical workload
gate with all 206 exact mutation identities and outcomes, 102 test
identities, whole-file selection and cleanup. The trace confirmed entry
redirection for 214 forks in each candidate, including the eight baseline
forks. All measurements below include diagnostic overhead.

| Generated framework maps | Stock wall / CPU | Bundled wall / CPU | Stock → bundled mutant startup CPU | Stock → bundled controller CPU |
| --- | ---: | ---: | ---: | ---: |
| Runtime visible | 14.86 s / 40.41 s | 23.36 s / 48.09 s | 13.89 → 11.63 s | 4.60 → 14.27 s |
| Hidden, artifacts stored under Vitest dist | 15.91 s / 45.53 s | 15.73 s / 43.70 s | 16.13 → 13.58 s | 5.10 → 6.29 s |

Bundling saved around 11–12 ms of startup CPU per mutant in both captures.
The initial bundle exposed large generated framework maps. Vitest's
controller reads external source and map files during stack processing;
the largest generated map was about 956 KiB. The controller CPU increase
suggests repeated error stack mapping contributed to that regression.
The follow-up kept framework maps as offline artifacts, omitted their
runtime reference, and put the generated files under Vitest's standard
stack-filter prefix. The settings changed together, and the experiment
did not measure map decoding or file-read latency separately. Project and
mutant maps stayed under their stock path.

The second capture recovered the large controller regression, but the
command wall difference was **0.18 s** and sampled peak RSS rose from
**775 MB to 914 MB**. Keep this as a diagnostic and a shared-runtime
identity reference for a possible startup-snapshot experiment. These
captures provide no new unprofiled score.

- [Runtime-visible maps run](https://github.com/RuiCostafrg/oximut/actions/runs/44737168938) and [verified data](mapped.json).
- [Hidden maps run](https://github.com/RuiCostafrg/oximut/actions/runs/44738285846) and [verified data](hidden.json).
- Raw archives, source inventories, build metadata, logs and unpacked
  evidence are saved at `/root/oximut-evidence/vitest-fork-bundle/{native,hidden}`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-bundle -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py \
  /root/oximut-evidence/vitest-fork-bundle/hidden \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```

## Snapshot feasibility checkpoint

A separate Linux ARM64 probe bundled the eager framework code into a
Node 26.3.1 user startup snapshot. It deferred Vitest IPC registration and
the ESM bridge until restore, and restored the canonical Node Console
constructor from a captured reference. Distinct fresh processes acknowledged
start and stop, and most rolling-fixture outcomes matched. The asynchronous
waiter timed out. The diagnostic logged the waiter entering, the other slot
running the release job and completing, and **zero timer ticks** from the
waiter. A minimal snapshot with no Vitest graph passed its timer check.
The bundled snapshot therefore failed the functional gate and remains
unscored. The retained log is at
`/root/oximut-evidence/vitest-fork-bundle/snapshot-local/rolling-timer-fixture.log`.
Node's snapshot builder emitted compatibility warnings for VM, async hooks,
module, and performance hooks.

```sh
scratch=$(mktemp -d "$PWD/.oximut-snapshot.XXXXXX")
node benchmarks/experiments/vitest-fork-bundle/snapshot.mjs "$scratch"
node --build-snapshot --snapshot-blob="$scratch/vitest.blob" "$scratch/seed.cjs"
OXIMUT_SNAPSHOT_BLOB="$scratch/vitest.blob" \
  NODE_OPTIONS="--require=$PWD/benchmarks/experiments/vitest-fork-bundle/snapshot-flags.cjs" \
  node --test --test-name-pattern='rolling forks' packages/vitest-adapter/test/shared-lanes.test.mjs
```

## Snapshot qualification follow-up

The follow-up separates three worker-state boundaries. The fake-timer
package probes timer support by scheduling and clearing a real timer when
its default instance is initialized. Deferring that initialization until
the first use after restore made the real-timer rolling fixture pass without
the diagnostic timer that masked the failure. The snapshot loader also
removes its private flag from the restored worker's inherited Node arguments.
An ordinary Node child otherwise inherits the flag and launches the snapshot
instead of its requested script.

The first two native attempts did not pass Colord's baseline. They used a
separate loader for Vitest's lazy globals registration module, which
created a second collector even though explicit imports of Vitest's public
API were already redirected. Including that registration module in the
framework bundle preserves collector identity. Registration still happens
when Vitest sets up the test environment after restore. The local fixture
checks that an explicitly imported test function and the global test
function share an identity, that fake timers advance and return to real
timers, and that a nested fork runs its own entrypoint. A separate local
Colord baseline ran all 102 tests through four distinct restored forks.

The paired stock score from the rolling study bounds the required gain.
The medians were 45.01 Oximut CPU-seconds and 15.49 Stryker wall-seconds.
Computing the bound on each of the five pairs gives a median **13.90
CPU-seconds** to remove even with perfect utilization of four CPUs.
The earlier hidden-map diagnostic recorded 16.13 CPU-seconds in all mutant
startup, including the parts of Node boot that a snapshot must still
perform. So a faster worker startup is worth measuring, and a reduction
at that checkpoint alone cannot establish the 0.50× wall target.

The native follow-up records blob generation and loading as separate
diagnostic scopes. Any scored configuration that builds its image per
invocation must include that build inside the timed process tree.

The first native capture with the globals fix passed workload
qualification, including the 206 exact mutant identities and outcomes
and the 102 tests. The capture's stricter trace check initially failed
because two final stop messages arrived about 0.1–0.2 ms after their
workers' clean exits. The raw trace shows both results, stop requests and
acknowledgements. The updated verifier replays the unchanged archive and
accepts that ordering only for a clean exit with one late acknowledgement
and intact worker CPU checkpoints. Node documents that streams can
remain open when the exit event arrives.
[Node child process event semantics](https://nodejs.org/download/release/v26.3.1/docs/api/child_process.html#event-exit)

In this one instrumented native pair, mutant startup CPU fell from
**18.32 s to 10.35 s**, while controller CPU rose from **5.76 s to 8.98 s**.
Whole-command CPU fell from **52.45 s to 47.71 s**, but wall increased
from **19.09 s to 19.55 s** before counting **0.76 s** for image generation.
The raw archive is at
`/root/oximut-evidence/vitest-fork-bundle/snapshot-native-globals/artifact.zip`.
It is a startup attribution result, separate from the unprofiled score.
[Native diagnostic attempt](https://github.com/RuiCostafrg/oximut/actions/runs/44783788870)

## Offline framework source diagnostic

Three instrumented runs on one Ubuntu x86-64 runner measured the effect
of keeping the generated framework seed at its original path. The snapshot
arms used the same 9.63 MB blob and bridges. The third arm moved the seed
out of the runtime path and retained it in the archive. The change applies
only to the generated framework source. Vite still supplies the project
and mutant source maps.

| Instrumented arm | Command wall | Command CPU | Peak RSS | Mutant startup CPU | Controller CPU |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stock forks | 16.23 s | 45.44 s | 813 MB | 16.06 s | 5.08 s |
| Snapshot, seed present | 16.65 s | 42.09 s | 981 MB | 8.82 s | 8.46 s |
| Snapshot, seed offline | 13.15 s | 36.01 s | 756 MB | 8.74 s | 4.30 s |

The controller made 1,490 synchronous reads of the generated seed
when it was present, totaling 1.43 GB of returned bytes and 1.46 s
inside the measured read calls. The offline arm recorded zero such
reads. The observed controller CPU difference was 4.16 s. That
difference includes work outside the timed read calls. There was one
fixed-order observation per arm. Separate diagnostics billed 0.69 s wall
and 0.86 s CPU to bundling and building the image, outside the table.

The three arms reconciled the exact 206 mutant identities and outcomes,
102 test identities, whole-file selection and cleanup, with 214 fresh
workers per arm including the baseline. The offline arm still spent
10.66 s worker CPU through first collection and 8.99 s thereafter.
The diagnostic supports scoring the offline-source image with its build
inside the timed command. The startup phase alone has limited remaining
headroom under the fresh-process requirement.

- [Native run](https://github.com/RuiCostafrg/oximut/actions/runs/44785071847)
- [Verified diagnostic data](snapshot-source-io.json)
- Raw archive, build inputs and worker evidence at
  `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-source-io`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-source-io -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py --snapshot \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-source-io \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```

## Unprofiled snapshot score

The offline-source snapshot improves Oximut's paired wall by **10.8%**
and CPU by **16.2%**, with image generation and deletion charged to
every invocation. It meets the CPU and RSS targets against Stryker.
It needs a further **44.5% wall reduction** from its median ratio to
reach the wall target.

| Metric | Stock / Stryker | Snapshot / Stryker | Snapshot 95% bootstrap interval | Target |
| --- | ---: | ---: | ---: | ---: |
| Wall | 0.999× | 0.901× | 0.898–0.928× | ≤0.50× |
| Process-tree CPU | 0.887× | 0.745× | 0.740–0.762× | ≤0.80× |
| Sampled peak aggregate RSS | 0.635× | 0.629× | 0.618–0.637× | ≤0.80× |

Five measured pairs and one warmup ran serially in alternating order with
four workers on Ubuntu 24.04 x86-64 (Intel Xeon 6975P-C), using Node 26.3.1,
Vitest 5.1.0 and Stryker 10.1.1. Both Oximut arms used the same release
executable and adapter. The candidate added a per-command image builder and
the fork-only snapshot loader. It had a median wall of 14.42 s and CPU of
38.17 s. Stryker in its pairs had medians of 15.99 s and 51.47 s.

All 36 warmup and measurement rows passed historical workload and cleanup
checks. Both mutation engines matched all 206 exact edited-source outcomes.
Each Oximut invocation used the same per-mutant file selection and planned
tests and passed the 102-test baseline. Existing worker-local bail after
failure makes the number of reported mutant tests vary between repeats.
Measured stock and snapshot runs both reported between 3,617 and 3,672.
Separate instrumented runs above verified fresh fork and selector
lifecycle. The RSS sampler had zero read errors and observed maximum gaps
of 70 ms for snapshot Oximut and 108 ms for paired Stryker.

Keep the framework-only snapshot as an experimental candidate. With four
CPUs fully utilized, the measured snapshot pairs still require a median
**6.46 CPU-seconds** of additional savings before half Stryker's wall is
possible. That lower bound excludes idle time, so the command may need a
larger reduction. The next two diagnostics examine controller stack
remapping and repeated first collection.

- [Native score run](https://github.com/RuiCostafrg/oximut/actions/runs/44786058083)
- [Verified score and paired intervals](snapshot-score.json)
- Raw archive, logs and metadata at
  `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-score`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-score -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/score.py \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-score \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture/qualification/measurements.jsonl
```

## Controller stack remapping after taking the seed offline

The offline snapshot control mapped 202 failure errors. The mapping
calls used **22.8 ms controller-thread CPU** and **65.4 ms elapsed** in
total. The skip arm bypassed only the mapping of these task errors and
qualified against the same 206 mutant outcomes and 102 test identities.

| Instrumented snapshot arm | Command wall | Command CPU | Controller CPU |
| --- | ---: | ---: | ---: |
| Time normal mapping | 13.29 s | 36.77 s | 4.37 s |
| Skip task error mapping | 13.50 s | 36.88 s | 4.34 s |

Both arms recorded zero successful reads of the offline seed. The
measured mapping scope leaves too little CPU to close the wall gap,
and the skip observation showed no command gain. Keep the normal
mapping path. These captures are diagnostics.

- [Native run](https://github.com/RuiCostafrg/oximut/actions/runs/44787320951)
- [Verified diagnostic data](snapshot-stack.json)
- Raw evidence at `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-stack`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-stack -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py --followup stack \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-stack \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```

## First-collection CPU samples in restored workers

The inspector sampled the main thread of 25 restored mutant workers,
selected by the first byte of their stable mutation ID. The window
starts when the worker receives `run` and ends at the first
`onCollected` IPC send. Among 2,747 leaf samples, 237 were in
Vitest's `_runInlinedModule`, 217 in Node's `vm.Script`, 173 in
garbage collection, 140 in `writeBuffer`, and 360 were idle.
The first two frames together account for 19.0% of the non-idle
samples. The sampling excludes other threads and can oversample code
around inspector and IPC boundaries. It does not allocate whole-fork
or process-tree CPU to those functions. The 25 selected workers are
part of the otherwise complete, workload-qualified capture.

The repeated inlined module path and its IPC traffic are the
highest-priority candidates for a combined ablation. Native ESM could
avoid both paths. Vitest's native flag alone bypasses the Vite hook that
serves the instrumented sources. A useful test must add an equivalent
worker-local source loader, preserve per-file isolation and run the full
206-mutant workload before timing claims. A local compatibility probe
passed the 102-test Colord baseline after a TypeScript emit pass removed
type-only re-exports and a resolver hook restored extensionless imports.

- [Native sampled run](https://github.com/RuiCostafrg/oximut/actions/runs/44787990210)
- [Verified diagnostic data](snapshot-collection-cpu.json)
- Raw evidence at `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-collection-cpu`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-collection-cpu -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py --followup collection-cpu \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-collection-cpu \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```

## Native project modules in fresh snapshot forks

The Colord-only loader serves precompiled JavaScript and overlays the
controller's current instrumented resident sources at their original
file URLs. It resolves Colord's extensionless imports and scopes the
project module cache to each selected test file. The baseline keeps
the original Vite path. A local fixture verified different outcomes
for two selectors, isolated module state across files, and worker exit
with and without the framework snapshot.

The native Linux experiment reached the intended path. The trace
reconciles 206 mutant workers with the report, including each active
source loaded under its exact selector. Test identities, whole-file
selection, outcomes and cleanup all qualified. The controller saw
**zero** fetch RPCs in native mutant workers, down from 8,117 in the
offline-snapshot control.

| Instrumented arm | Command wall | Command CPU | Mutant startup CPU | Mutant CPU through first collection |
| --- | ---: | ---: | ---: | ---: |
| Stock Vitest forks | 19.44 s | 52.76 s | 18.53 s | 11.19 s |
| Offline snapshot, Vite modules | 17.55 s | 44.09 s | 10.83 s | 11.76 s |
| Offline snapshot, native project modules | 21.02 s | 53.55 s | 15.62 s | 13.12 s |

The native path increased CPU in both worker phases despite removing
fetch RPCs. Its wider costs include the alternate Vitest runtime,
Node's loader and fresh module linking, the module table, and
controller-side emit. One fixed-order diagnostic cannot apportion the
increase. Retain the Vite module runner and skip an unprofiled score
for this candidate. The 0.901× snapshot wall ratio remains the best
qualified score.

- [Native diagnostic](https://github.com/RuiCostafrg/oximut/actions/runs/44789560220)
- [Verified diagnostic data](snapshot-native-project.json)
- Raw evidence at `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-project`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-native-project -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py --followup native-project \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-project \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```

## CPU after first collection

On the qualified snapshot capture, the mutant forks used **9.37 CPU
seconds** from first collection to finish. That interval occupied
**3.22 seconds** of the last-finishing paths across 52 batches.

The inspector sampled 25 forks from their first collected message to
the finish message. Of 1,553 sample stacks, 39 were idle, 317 included
Vitest's `processError`, and 167 included source-map position lookup
within that error path. The tasks themselves also accounted for many
samples, including Colord's color conversion and parsing functions.
These are main-thread sample counts from a subset with inspector
overhead. They give the stack overlap and relative concentration,
without estimating process-tree CPU savings.

The controller stack ablation does not cover this cost. A separate
Colord-only worker ablation returns the ordinary Chai assertion's name
and message without formatting the diff or materializing the stack.
It keeps non-Chai and caused errors on the original path. The experiment
checks every mutant fork's mode and counts assertions that reach the
replacement path. It is a diagnostic of the error payload, which can
be observed by Vitest retries, callbacks, and custom reporters.

- [Native run](https://github.com/RuiCostafrg/oximut/actions/runs/44790961844)
- [Verified diagnostic data](snapshot-execution-cpu.json)
- Raw evidence at `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-execution-cpu`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-execution-cpu -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py --followup execution-cpu \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-execution-cpu \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```

## V8 baseline compiler tier

One native diagnostic used the same offline snapshot in both arms and
set `--max-opt=1` in exactly the 206 mutant forks of the second arm.
This caps V8 optimization at its baseline compiler for each short-lived
mutant process. Both arms retained the exact mutant identities and
outcomes, test identities, whole-file selections, and clean exits.

| Instrumented offline snapshots | Command wall | Command CPU | Worker CPU after collection |
| --- | ---: | ---: | ---: |
| Default tier | 13.33 s | 36.78 s | 9.11 s |
| Baseline tier only | 13.61 s | 36.34 s | 7.94 s |

Post-collection worker CPU fell by 1.17 s. Startup and first collection
used another 0.87 worker CPU seconds, leaving a small command CPU
difference and slightly higher wall. This fixed-order comparison
leaves the default tier as the scoring candidate.

- [Native run](https://github.com/RuiCostafrg/oximut/actions/runs/44829000980)
- [Verified diagnostic data](snapshot-baseline-tier.json)
- Raw evidence at `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-baseline-tier`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-baseline-tier -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py --followup baseline-tier \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-baseline-tier \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```

## Disk-backed V8 wrapper cache feasibility

A local Linux ARM64 replay captured 46 exact wrapper inputs from Vitest's
successful 102-test baseline. Twenty alternating fresh Node processes
per arm compiled those wrappers. No module body ran in the timed loop.
The median CPU was 9.61 ms for compilation from source and 20.46 ms
for hashing, reading, and consuming the prebuilt disk cache. All cached
scripts were accepted. Cache generation had its own measurement.

This rules out promoting the straightforward disk-backed design from
the local replay. It gives no native mutation score. The earlier native
stock probe also measured just 0.61 worker CPU seconds in synchronous
wrapper compilation across all 206 mutants. Neither measurement bounds
later lazy compilation. Obtaining cache data after representative
module execution would need a separate experiment that charges the
producing work and evaluates user code again in every fresh fork.

Raw wrapper inputs, capture and replay scripts, the 102-test baseline
report, and per-process timings are retained at
`/root/oximut-evidence/vitest-fork-bundle/wrapper-cache-local`.

```sh
evidence=/root/oximut-evidence/vitest-fork-bundle/wrapper-cache-local
node "$evidence/replay.cjs" build "$evidence/wrappers"
node "$evidence/replay.cjs" control "$evidence/wrappers"
node "$evidence/replay.cjs" disk "$evidence/wrappers"
```

## Worker assertion serialization

The Colord-only compact error arm handled 202 ordinary Chai failures.
Both arms retained all 206 exact mutant identities and outcomes, all
102 test identities, the whole-file selections, and clean worker exits.

| Instrumented offline snapshots | Command wall | Command CPU | Worker CPU after collection |
| --- | ---: | ---: | ---: |
| Full errors | 13.77 s | 37.53 s | 9.29 s |
| Compact errors | 13.16 s | 34.92 s | 6.70 s |

The mutant reports contained the same failure messages for 205 entries.
One killed mutant stopped at a different assertion after seven versus
eight passing tests. The 12 existing unprofiled stock and offline
snapshot reports include both of those first failure messages, so the
difference was already possible with the normal error path.

The observed command change was 0.61 s less wall and 2.61 s less CPU.
It warrants a paired unprofiled Colord score. Production Vitest
customizations can observe the full error payload, so this diagnostic
does not establish an automatic fast path for arbitrary projects.

- [Native run](https://github.com/RuiCostafrg/oximut/actions/runs/44829555378)
- [Verified diagnostic data](snapshot-compact-errors.json)
- Raw evidence at `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-compact-errors`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-compact-errors -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py --followup compact-errors \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-compact-errors \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```

## Constant mutation switches in the worker

The diagnostic specialized 112,692 switch expressions while loading
modules in the 206 fresh mutant forks. Each fork reported its traced
selector and at least one matching switch. The worker replaced the
comparison with a Boolean of the same source width and evaluated the
modules in Vitest as usual. Both arms retained the exact 206 mutant
outcomes, 102 test identities, and whole-file selections.

| Instrumented offline snapshots | Command wall | Command CPU | Worker CPU after collection |
| --- | ---: | ---: | ---: |
| Environment lookup per switch | 12.77 s | 33.52 s | 7.70 s |
| Constant switches | 12.29 s | 32.88 s | 6.68 s |

In this fixed-order run, the pass incurred another 0.23 CPU seconds
through first collection and saved 1.02 CPU seconds after collection.
The observed 0.49 s wall reduction is worth testing alongside larger
changes. This controlled fixture uses the selector as a launch input.
The ablation does not support projects that deliberately write the
reserved environment variable or observe the generated switch source.

- [Native run](https://github.com/RuiCostafrg/oximut/actions/runs/44830954410)
- [Verified diagnostic data](snapshot-specialize-selector.json)
- Raw evidence at `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-specialize-selector`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-specialize-selector -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py --followup specialize-selector \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-specialize-selector \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```

## Unprofiled compact assertion score

One warmup pair and five alternating measured pairs compared stock
Oximut to the offline snapshot with compact assertions. The timed
candidate includes building and deleting the snapshot. Every row in
the 36-measurement campaign passed the historical workload and cleanup
checks on four native Ubuntu 24.04 x86-64 workers.

| Paired median ratio | Wall | CPU | Sampled peak aggregate RSS |
| --- | ---: | ---: | ---: |
| Compact snapshot vs Stryker | 0.848× | 0.685× | 0.622× |
| Compact snapshot vs stock Oximut | 0.857× | 0.772× | 0.936× |
| Campaign target vs Stryker | ≤0.50× | ≤0.80× | ≤0.80× |

The 95% bootstrap intervals against Stryker were 0.828–0.853 wall,
0.674–0.686 CPU and 0.588–0.660 RSS. The command medians were 13.09 s
wall and 34.39 s CPU for the compact snapshot, with 15.52 s wall and
50.23 s CPU for paired Stryker. The remaining wall gap is about
5.33 s against half the latter median. Across the five pairs, the
additional CPU savings needed even at full four-core utilization had
a median of 3.37 s. These comparisons use their own allocations.
The previous 0.901× snapshot score is a separate campaign.

- [Paired native run](https://github.com/RuiCostafrg/oximut/actions/runs/44831075244)
- [Verified paired data](snapshot-compact-score.json)
- Raw evidence at `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-compact-score`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-compact-score -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/score.py \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-compact-score \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture/qualification/measurements.jsonl
```

## One project bundle per selected test file

The controller bundles each Colord test file and its local imports
while overlaying the current instrumented resident code. Vite serves
the emitted module under the original test URL, and its normal
evaluator runs it in each fresh snapshot process. Bundles remain
separate across selected files, and the original baseline still uses
the normal transform path.

The native trace confirmed 218 bundled test evaluations, each within
the assigned whole-file selection. For all 206 mutants, at least one
evaluated bundle contained the source assigned to that selector. The
historical mutant outcomes, 102 test identities, and fork teardown
checks all passed.

| Instrumented offline snapshots | Command wall | Command CPU | Worker CPU through first collection | Worker CPU after collection |
| --- | ---: | ---: | ---: | ---: |
| Stock module graph | 16.44 s | 45.31 s | 13.05 s | 11.01 s |
| Per-test project bundle | 15.83 s | 42.69 s | 7.92 s | 13.46 s |

Fetch RPCs fell from 8,357 to 424. The sum of first-collection
intervals along last-finishing batch paths fell from 4.85 s to
2.77 s. Later worker work and the finish path rose in the bundled
arm. The fixed-order comparison leaves their causes open. The next
unprofiled candidate combines the bundle, compact assertions, and
constant switches to test the net effect with all setup and cleanup
charged.

- [Native run](https://github.com/RuiCostafrg/oximut/actions/runs/44831876222)
- [Verified diagnostic data](snapshot-bundle-project.json)
- Raw evidence at `/root/oximut-evidence/vitest-fork-bundle/snapshot-native-bundle-project`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-snapshot-bundle-project -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py --followup bundle-project \
  /root/oximut-evidence/vitest-fork-bundle/snapshot-native-bundle-project \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```
