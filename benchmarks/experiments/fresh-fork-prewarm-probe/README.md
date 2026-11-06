# Fresh fork startup overlap diagnostic

This standalone experiment prepares one **unused fresh OS process** during the
preceding Vitest job. Each assigned worker runs exactly one specification before
normal termination. It uses pinned Vitest 4.1.12's actual ForksPoolWorker and
unchanged PoolRunner start/run/stop/RPC lifecycle. A launcher imports the real
fork entry, acknowledges actual framework import completion, and applies the
current selector before Vitest's start handler can import setup files. It does
not bundle Vitest, reuse an executed worker, or change the Oximut product.

The stock and candidate arms run byte-identical fixtures through one controller
each, isolate:true, one active worker, and four independently declared jobs:
baseline, mutant A, cleared selector, mutant B. They require the exact eight
test identities and outcomes, fresh PIDs/start identities/realms, clean local
ESM/external CJS/external ESM/global/env/builtin/spy/fake-clock state, and
matching selector observations in a real setup file and at module top level.
Baseline selectors must be absent, rather than empty environment variables.
The real stale-selector control must fail the independent selector oracle with
exit code 1; an unrelated failure, signal, timeout, forced cleanup, or failed
Vitest close/spare disposal cannot satisfy that control.

The fixed controller deadline is 45 seconds. Every controller has a private
process group, verified empty on completion. Final disposal explicitly stops
the last unused spare, and its cost is included. Readiness/error/exit evidence
remains required, including when a job claims a partly loaded process. Waiting
for that process happens inside the measured job call. Message subscription
wrappers preserve on/off semantics.

## Run

Use the locked install and pinned Node on Linux:

~~~sh
npm ci --ignore-scripts
node --test benchmarks/experiments/fresh-fork-prewarm-probe/test.mjs
python3 -m unittest discover -s benchmarks/experiments/fresh-fork-prewarm-probe -p test_measurement.py -v
prewarm_evidence=$(mktemp -d /tmp/fresh-fork-prewarm.XXXXXX)
node benchmarks/experiments/fresh-fork-prewarm-probe/probe.mjs --output "$prewarm_evidence/structural"
python3 benchmarks/experiments/fresh-fork-prewarm-probe/measure.py --output "$prewarm_evidence/paired"
~~~

Output directories must be new. The dedicated native workflow stages committed
source before prerequisites, retains dependency/test/run diagnostics even on
failure, and does not retry. Hard cancellation or runner loss can prevent upload.
Its per-ref queue preserves the running attempt and up to 100 pending attempts.

## Measurement scope

The paired driver reuses benchmarks/run.py's unchanged wait4 process-tree CPU,
fresh smaps_rollup aggregate RSS and owned-process cleanup sampler. It runs one
warmup and five pairs, alternating order. Wall, user/system/total CPU, peak
aggregate RSS and peak/observed process counts cover the **whole Node probe
command**, including fixture creation, hashing, its controller, workers, final
unused spare and teardown. The external Python sampler's CPU is excluded and
its memory reads perturb wall. This is a prototype comparison, not the Oximut
CLI; no Oximut/Stryker ratio, resources.phaseWallMs, 102-test/10-mutant colord
qualification, performance target or release qualification is produced.

The driver independently revalidates raw controller results, exact fixture
inventory/state/selectors, worker joins, lifecycle and input closure before
computing paired prototype ratios and deterministic bootstrap 95% intervals.
Missing/failed/incomparable pairs produce no comparative result. Independent
medians and nested timing scopes must not be added.

Every parent timestamp uses that controller's performance.now clock. Framework
readiness is the **parent's receipt** of the post-import acknowledgement, an
upper bound that includes IPC latency. Child import duration is a separate
child-local interval. Startup overlap intersects the spare's spawn-to-receipt
interval with the preceding run-RPC interval; ready-before-run-response and
ready-before-drain are separate observations. Neither early spawn nor a partly
overlapping interval proves the fork was ready before the previous job ended.
There are no added sleeps to manufacture overlap.

Local Android/PRoot runs establish only the fixture and harness behavior.
Native prototype evidence, then full native CLI/oracle evidence, is required
before any product performance conclusion. The final unused spare may increase
CPU and memory even if startup overlap reduces wall time.
