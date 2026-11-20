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
larger reduction. Controller stack remapping and repeated first collection
are the next two measured paths to investigate. Stock Vitest's native ESM
switch bypasses the transform hooks that deliver the instrumented mutant
and baseline source and cannot serve as a direct ablation.

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
