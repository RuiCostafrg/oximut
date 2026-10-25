# M2 fresh/persistent comparison

Run on the same Android/Termux-hosted Linux arm64 PRoot environment as the M0
baseline. Node is the pinned Linux 26.3.1 build; Vitest is locked to 4.1.10. The
debug Rust CLI uses one isolated test fork at a time. See `metadata.json` for
the actual platform, Rust version, binary hash, and adapter/lockfile hashes.
The working tree included the uncommitted M1 and M2 implementation.

Reproduction:

```sh
export PATH=/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin:$PATH
cargo build -p oximut-cli --locked
python3 benchmarks/m2.py --repetitions 3 --warmups 1 --output NEW_DIRECTORY
```

One warmup pair precedes three measured pairs for each of the M1, M2, and
stateful fixtures. Execution-mode order alternates, and each pair uses the same
seeded mutant order. Every run includes the original baseline and final cleanup;
persistent mode additionally validates disabled shared transforms. Test forks
remain fresh in both modes. Stateful source uses the documented static-transform
fallback because it references `process`, while its controller is reused.

`measurements.jsonl` contains all elapsed-time samples and commands. Each sample
has a JSON mutation report and captured log; `summary.json` contains medians and
fresh/persistent ratios. Warmups are excluded from the summary. The harness
refuses to produce a summary if outcomes disagree or any run is incomplete.

Measured medians (fresh → persistent): M1 **8.466s → 5.679s (1.49×)**,
M2 **16.964s → 6.711s (2.53×)**, and stateful **13.787s → 9.934s (1.39×)**.
Persistent execution was faster in each measured pair. All runs agreed on the
14 mutant outcomes: 12 killed and two survived across the three fixtures.
Every original and disabled-switch baseline passed.

These small fixtures demonstrate controller reuse under the documented isolation
policy. Measurements are end-to-end wall time on a shared host, without CPU
affinity or thermal control. They establish neither CPU/memory savings nor a
speed comparison against Stryker, and should not be extrapolated to arbitrary
projects. Fresh execution remains the default.
