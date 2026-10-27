# Compatibility and support boundary

Oximut is a pinned feasibility prototype. The only demonstrated host is Linux
arm64 in Ubuntu userspace under Android/Termux PRoot. The repository does not
claim a broad OS or runtime support range. A compatible dependency version is
not evidence that every project using it is supported.

## Runtime matrix

| Component | Required or pinned value | Evidence and boundary |
| --- | --- | --- |
| Host kernel/process model | Linux with readable `/proc`, `prctl(PR_SET_CHILD_SUBREAPER)`, process signaling, and `waitpid` | Required by the Python process supervisor and Rust resource/process-tree controller. Windows and macOS are unsupported. |
| Demonstrated host | Android 12, aarch64, Ubuntu userspace under Termux PRoot; Linux kernel reported as `6.17.0-PRoot-Distro` in the M0 lifecycle record | M0/M2 and M6 fixtures and benchmarks ran here. This is one environment, not a claim for all Linux distributions, kernels, or containers. |
| Node.js | `26.3.1`, Linux executable | Pinned in `package.json` and used in the recorded M0/M2 runs. Adapter code records `process.version` but does not reject other Node versions; other versions have no compatibility evidence. Android/Termux Node cannot replace the Linux binary when Linux native dependencies are loaded. |
| npm | `11.16.0` | Pinned in `package.json`; use `npm ci` with the committed lockfile. |
| Python | `3.13` in the pinned feasibility environment | Linux process supervision requires Python 3 and the standard library. The recorded supervisor executable is the Termux host Python 3.13.13. Other Python versions are not separately validated. |
| Rust / Cargo | `1.96.0`, minimal toolchain with `clippy` and `rustfmt` | Exact toolchain in `rust-toolchain.toml`; workspace declares Rust 1.96.0. |
| Oxc crates | `0.152.0` | Exact versions pinned in `Cargo.toml` and `Cargo.lock`. |
| Vitest | `4.1.12` exactly | Both adapter entrypoints reject any other version at initialization. The lockfile pins this version. Historical M0/M2 lifecycle and benchmark records used `4.1.10`; those results are not current-version support evidence. |
| `@vitest/coverage-v8` | `4.1.12` | Lockfile-pinned with Vitest; used for M3 attribution. |
| Stryker (benchmark dependency only) | Core and Vitest runner `10.0.0` | Pinned for the baseline benchmark harness; not required to run Oximut. |

Reproduce the current pinned setup and project checks with:

```sh
npm ci
cargo build --locked -p oximut-cli
python3 -m unittest discover -s tests -v
node --test packages/vitest-adapter/test/*.test.mjs
```

The integration checks are fixture acceptance evidence on the documented Linux
host, not a cross-platform test matrix. The CLI also uses Linux `/proc` process
and RSS accounting; M5 resource claims therefore do not transfer to another OS.
Memory control is sampled admission and cancellation, not a newly installed hard
limit. A container's visible cgroup limits may further constrain it.

## Project and execution scope

The adapter targets one Vitest project using the Node environment, one isolated
fork, one worker per execution slot, sequential test files/tests, and the pinned
Vitest lifecycle APIs. Resolved settings outside required isolation/resource
constraints, including non-Node environments, fail explicitly. The runner sets
sequential-file execution at the root and validates resolved project pool,
isolation, worker count, concurrency, environment, and single-project scope.
Persistent mode falls back to a fresh controller for project features such as
custom Vite plugins, global setup, or symlinked project inputs. It still uses
fresh test forks. A controller fallback does not bypass M5
configuration validation: a multi-project configuration remains unsupported in
either execution mode.

Instrumentation supports a deliberately small JS/TS expression set: boolean
literal inversion, comparison boundary changes, and selected arithmetic
replacements. Type-only syntax, declaration files, ambient declarations, enums,
namespaces, and decorator expressions are excluded; unsupported contexts are
diagnosed. This is not TypeScript semantic checking. Source scope is JS/TS ESM
through the supported Vitest/Vite transform graph. Browser projects, Jest,
framework-specific formats, native/custom VM execution, and arbitrary plugin
graphs have no support claim.

Test selection is opt-in and retains whole test files when attribution is sound;
uncertain attribution falls back to broader execution. External files, services,
time, randomness, and other undeclared behavior remain outside outcome-cache
correctness guarantees. Outcome caching requires the user assertion
`--cache-inputs-complete`; do not use it when relevant inputs are uncontrolled.
Concurrent workers require tests with isolated external inputs and outputs.

For milestone behavior and known limitations, see [M1](m1.md), [M2](m2.md),
[M3](m3.md), [M4](m4.md), and [M5](m5.md). The schema and command-line outcomes
are described in [the report reference](report-schema.md).

The [R1 contract audit](contract-audit.md) maps the supported behavior, rejected
settings, and conservative fallbacks to repository acceptance checks.
