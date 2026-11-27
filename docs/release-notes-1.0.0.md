# Oximut 1.0.0

Oximut mutation-tests JavaScript and TypeScript projects using a single Node
Vitest project. The first stable release targets Ubuntu 24.04 x86-64 with
glibc 2.39, Node 26.3.1 and Vitest 5.1.0. See the
[compatibility contract](https://github.com/RuiCostafrg/oximut/blob/v1.0.0/docs/compatibility.md)
for prerequisites and limits.

## Changes since RC.3

- Adds equality, logical, unary, update, and assignment operator families.
  Use `--mutators` to select the families used by a run.
- Counts timed-out mutants as detected. The report keeps `timeout` as a
  distinct verdict and exposes the detection count.
- Improves selection on large suites and stops each fresh mutant's test job at
  its first failure. Whole-file selection remains conservative around tests
  that share state or hooks.
- Uses static mutations when source can interfere with the injected selector.
  Cached instrumentation from the previous selector rule is regenerated.

## Installation

The GitHub Release contains the source archive, validated Linux prefix,
checksums, provenance, and installation evidence. The npm and Homebrew
channels use the same validated prefix. Release channels may become available
at different times as their own checks and publication complete.

- Follow the [source install guide](https://github.com/RuiCostafrg/oximut/blob/v1.0.0/docs/install.md)
  with the curated archive attached to this release.
- Once npm publication is complete, install `oximut@1.0.0` and run
  `npx oximut --version`.
- Once the tap formula is published, install with `brew install RuiCostafrg/tap/oximut`.
- The [GitHub Action](https://github.com/RuiCostafrg/oximut/blob/v1.0.0/docs/distribution.md#github-action)
  installs an explicitly pinned release and verifies its provenance.

Resource admission uses periodic process-memory samples. See the
[resource contract](https://github.com/RuiCostafrg/oximut/blob/v1.0.0/docs/m5.md)
for its limits and the
[support policy](https://github.com/RuiCostafrg/oximut/blob/v1.0.0/SUPPORT.md)
for reporting a reproducible problem.
