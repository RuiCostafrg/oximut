# First cache profiling diagnostic

This is a single diagnostic repetition with no warmup, using the debug binary
built from M6 commit `f5197b0` plus the additive profiling changes recorded in
this archive's metadata source hashes. It is not a release performance estimate
or a comparison with the earlier M6 campaign. Full runtime metadata, binary and
source hashes, commands, reports, and raw measurements accompany this note.

Command (after `cargo build -p oximut-cli --locked`, using the pinned Linux Node):

```sh
python3 benchmarks/m6.py --fixtures m1 --tools oximut --scenarios incremental --changes --repetitions 1 --warmups 0 --output .oximut/cache-profile-first
```

The correctness gate passed. Prime had three outcome misses, warm reused all
three, and forced had zero hits; classifications agreed. The warm report records
prepare, post-baseline, and pre-publication fingerprint attempts. Each read
294,701,233 bytes across 10,078 successful file hashes (repeat reads count again).
Together these passes read 884,103,699 bytes. Input processing accounted for
99,364 of 99,710 measured fingerprint milliseconds; runtime identity and resource
policy were comparatively small. Runner hashing separately took 3,644 ms.
Warm wall time was 107.73 s; forced was 6.55 s. Do not compare these one-off debug
timings to M6's five-repeat results or infer a regression caused by telemetry.
A temporary Python validator dependency installation was started during the tail
of the campaign and later cancelled; this further disqualifies performance claims.
No Rust builds or test suites overlapped the campaign.

The next investigation is the full-content scan cost and repeated reads, while
preserving detection of input changes between baseline and publication. This
change does not optimize hashing, skip validation, or weaken the cache contract.

Validation for this implementation: all 32 Rust workspace tests, Clippy with
warnings denied, formatting, and this end-to-end gate passed. The locked Ajv
Draft 2020-12 validator accepted 114 reports: 110 from the M6 archive plus four
copies emitted here (prime, warm, forced, and the live report). The optional schema
fields therefore retain compatibility with the archived reports.
