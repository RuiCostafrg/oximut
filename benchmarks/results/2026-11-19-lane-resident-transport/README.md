# Compact resident modules on native controller runs

Base `62c08df8fc83c16e9430f48a230e5895776c40ec` (PR #69).
W11's scheduler frame already sends one copy of a shared set and restores each
lane's Arc in the slot. The slot used to expand all those copies again for Node.
[W11 diagnostics](../2026-11-14-batch-accounting/README.md)

The built-in controller advertises the exact private command
`run-shared-lane-resident-v1` in its initialize reply. For an eligible run, the
slot omits the lane resident members from the typed request before
`serde_json::to_value`. Eligibility requires a nonempty lane list and the same
Arc at the batch and every lane. Node requires one top-level resident array and
no lane copies, restores references for this message, and runs the regular
validator. The validator skips the equality stringifications only when both
operands reference the same array. It still validates each lane and resident
array. Active mutation payloads and their map checks remain in place.

Absent or unknown capabilities keep the expanded `run` encoding. The current
adapter accepts expanded runs from older Rust. Initialize still takes the full
request, including on cold lane runs, and warm and non-lane commands retain
their encoding. A cold eligible run constructs a second, compact value for
dispatch after serializing initialize. There is no new cross-message module
cache. Admission still uses the expanded 8 MiB limit with existing headroom.
The same deadline, response checks, cleanup, resource sampling, mutant order
and whole-file selection apply.

## Serialized bytes

The focused Rust test uses the pinned vendored manifest scopes (10 and 206
mutants), actual switched/probed code and maps, and the production run IPC
serializer. It takes the first matching mutants in manifest order and uses
fixed paths, identities, deadline charge and a representative whole-file
selection. These are constructed batches with real transforms and fixed
metadata. Each count includes the command envelope and newline.

| Workload | Lanes | Resident modules | Expanded bytes | Compact bytes | Bytes removed | Reduction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| colord | 4 | 5 | 239,076 | 72,960 | 166,116 | 69.48% |
| colord-scale | 2 | 20 | 3,771,979 | 1,636,303 | 2,135,676 | 56.62% |
| colord-scale | 4 | 20 | 6,286,579 | 2,015,203 | 4,271,376 | 67.94% |

Run the focused byte-count test to reproduce these figures.

```sh
CARGO_BUILD_JOBS=3 cargo test -p oximut-cli --locked colord_controller_wire_sizes -- --nocapture
```

The exact delta is one resident member per lane minus the longer command name.
Rust constructs and serializes fewer JSON values. Node parses fewer values and
the two-lane validator regression observes four resident-array stringifications
for expanded runs and zero after restoring compact aliases. Initialization
traffic, active mutation serialization and resident validation still contribute
to total cost. No local benchmark was run. These counts only measure run frames,
so wall, CPU and aggregate RSS effects are unmeasured.

## Focused validation

Developer checks ran on Linux aarch64 under PRoot with Rust 1.96.0, Cargo
limited to three build jobs, official Linux Node 26.3.1, npm 11.16.0 and
`npm ci --ignore-scripts` from the unchanged lockfile
(`6dc7a6c55362e0dc23c2c5395a7e3ecfeb7300337f75aa2c6ef610e3680abd92`
SHA-256). Android Node 26.3.1 passed the protocol cases but its dynamic-linker
namespace blocked the Rolldown addon. The Linux build ran all focused cases.

- The 104 Rust CLI tests and 11 process integration tests, Rust formatting
  and CLI Clippy with warnings denied.
- The 27 Node protocol and shared-lane tests, plus adapter format, lint and
  type checking. Lint reported existing warnings.
- The 21 native coordinator tests and two CLI shared-controller tests. The
  native wire regression checks the lane fields with both a legacy mock and a
  capable mock, plus batch order, deadlines, response identity and reaping.
  The Node controller tests exercise expanded commands for old-client
  compatibility.

## Promotion gate

The next gate is parent review followed by native Linux x86_64 unprofiled paired
runs against the exact PR #69 base, with the small and scale workloads at two and
four workers. Use matched parallelism, the existing build-integrity and
correctness gates, one warmup and five measured pairs. Compare wall, total CPU
and peak aggregate RSS against the base and Stryker with uncertainty intervals.
The project goals remain wall at most 0.50x and CPU/RSS at most 0.80x Stryker.
