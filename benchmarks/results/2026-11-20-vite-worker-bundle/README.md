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
