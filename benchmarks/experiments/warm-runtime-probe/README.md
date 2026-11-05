# Warm-runtime isolation experiment

This is a standalone structural probe of pinned Vitest 4.1.12, not a product mode
or performance benchmark. Fresh forks remain Oximut's default.

From the repo root, with its installed dependencies and Node 26.3.1:

```sh
node benchmarks/experiments/warm-runtime-probe/probe.mjs --output /tmp/oximut-stock-probe-new
node benchmarks/experiments/warm-runtime-probe/probe.mjs --case warmVm-true-separate --output /tmp/oximut-host-probe-new
```

Output directories must be new. The first command runs 12 stock cases. Every
case uses one controller/Vitest instance; `separate` calls
`runTestSpecifications` twice, whereas `batch` submits both files together.
Each case executes 16 assertions and records two state snapshots to a side
channel, without parsing console formatting. Requested and effective isolation
settings are recorded separately. Deliberately failing controls retain Vitest's
exit code 1 and failed assertion counts; they are never labeled fresh.

The optional warm-host case uses a custom `poolRunner` and a pinned private VM
entrypoint. It drains each job's stop/RPC lifecycle but preserves the host thread,
then explicitly terminates it at controller shutdown. It currently leaks host
state and is **unsafe for mutation jobs**. No adapter or CLI option enables it.

Every controller runs in its own process group with a 45-second deadline. The
probe waits for closure and verifies the group is gone; timeout/leak/error paths
fail rather than publishing a successful case. Generated projects are removed;
input metadata, snapshots, structured results and stdout/stderr remain in the
specified evidence directory.

[Observed results and limits](REPORT.md) and [latest native control metrics](BASELINE.md)
are kept separate. Local Android/PRoot timings are not qualified performance evidence.
