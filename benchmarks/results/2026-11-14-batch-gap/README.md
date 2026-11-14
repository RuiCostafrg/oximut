# Shared-controller batch gap, 2026-11-14

## Attribution before implementation

Started at main `7297840df3996c9e6098afbe6d37db41b294ee0b`. Read the README,
boundary-intervals.json and summarize.py on `origin/perf/per-mutant-cost-2`.
Its 234.85 ms inter-batch median is an overhead-bearing ARM diagnostic, not a
GitHub-runner estimate. A new native function-boundary capture on this ARM host
reproduces a large gap (315.76 ms median; host cost varies substantially).
This attribution justifies building on **PR #64, `perf/event-driven-handoff`,
`3c03ac4c4002f6455d537f86f85dc3866fa2e172`**, rather than duplicating its transport.
The branch fast-forwards to that base; main is untouched.

Apply `attribution.patch` to `7297840`, build the release CLI, then execute the
command in `attribution-metadata.json`. It adds CLOCK_MONOTONIC boundary marks,
with separate append-only trace files for the scheduler and native slot. It
also times the existing file/directory sync calls inside `cache::atomic`.
The patch is retained as diagnostic evidence and is **not compiled into the
implementation**. Timing includes trace append overhead; concurrent stages
can overlap. This is an Oximut-only capture, not a paired performance campaign.
Oximut completed 206 mutants: 202 killed, four survived, no errors, three
controllers / 105 jobs. The harness exits 1 because its cross-tool comparison
gate requires Vitest and Stryker rows, which were deliberately not run. No
cross-tool correctness pass or performance ratio is claimed for this capture.

Regenerate the attribution:

```sh
python3 benchmarks/results/2026-11-14-batch-gap/summarize.py \
  benchmarks/results/2026-11-14-batch-gap/trace 0 1 49 99
```

105 native jobs are original baseline, instrumented baseline, then 103 mutant
batches. The four excluded transitions are the two baseline transitions and
the two 50-job retirements (the latter include controller initialization).
`attribution.json` retains the 100 within-controller mutant intervals plus all
excluded intervals. Raw traces, measurements and the local summary are retained
compressed. The summarizer's test covers clipping and overlapping process spans.

| Native/scheduler work inside reply → next send | Median ms |
|---|---:|
| Total, within-controller mutant batches | 315.76 |
| Result envelope and semantic/identity validation | 1.26 |
| Native post-job descendant verification (`Coordinator::leaked`) | 58.22 |
| Slot return → scheduler begins reading response (file publication + observation) | 26.56 |
| Scheduler `read_response` parsing/validation | 0.82 |
| Scheduler completion sampling / dispatch return | 50.78 |
| Batch assembly, including its second forced resource sample | 47.90 |
| Assembled batch → slot reads request (dispatch, sampling, serialization, atomic publication, polling) | 94.39 |
| Slot request parsing / entry to coordinator | 6.22 |
| `Fingerprint::take` traversal, metadata and racy content hashes | 15.74 |
| Fingerprint end → next controller send (comparison, args serialization) | 1.40 |
| Resource samples anywhere in the interval (union of spans, overlaps above) | 194.83 |
| File fsyncs anywhere in the interval (overlaps hand-off above) | 42.70 |
| Directory fsyncs anywhere in the interval (overlaps hand-off above) | 10.23 |

Independent medians are not additive. Resource samples include process discovery,
identity verification, RSS, host/cgroup availability, and ownership snapshots.
Request/response file writes, serialization and polling are bounded by the
hand-off rows; fsyncs are measured separately inside them. The old capture's
adapter-send/receive boundary differs slightly from these native boundaries;
channel delivery, controller parsing and OS scheduling are not separately timed.
No work is attributed to a narrower function without a clock around it.

## Implementation decisions

- Use #64's sockets/poll/pidfd hand-off, including its nonblocking request flush.
  This removes temporary request/response files and their durability syncs from
  the hot path; durable cache/report writes retain their existing guarantees.
  #64 already pins the paired benchmark baseline to main `7297840`.
- Reuse the mandatory completion sample when assembling the immediately next
  batch, through the existing `Budget::sample_admission` policy. A broad sample
  older than 50 ms is refreshed. Every accepted response still forces an exact
  broad sample; dispatch still forces exact accounting after a counter sample;
  capacity, cgroup pressure, interruption and exhaustion gates remain in place.
- Do not pipeline fingerprints across tests: a test can modify project inputs
  while it runs. Moving the snapshot earlier alone misses those writes.
  Do not overlap leak verification with new lanes: broad ownership verification
  would see legitimate next-batch children as leaks. The measured fingerprint
  cost is secondary to accounting and transport. Streaming lanes is deferred;
  fresh-process isolation, whole-file selection and test order are unchanged.

Validation and paired GitHub Actions results are recorded below after completion.
