# 1.0.0-rc.1 release notes (unpublished draft)

This candidate introduces a versioned source distribution for Oximut's Rust/Oxc
mutation engine and Vitest adapter. It is a release candidate for Linux source
installation, not stable 1.0.0. No prebuilt executable or macOS/Windows support is
claimed. This draft does not announce a published or validated release.

The candidate source archive contains Oximut's MIT-licensed source, installer,
adapter, fixtures, tests, docs, and the full nonvendored npm and Cargo lockfiles.
Installation compiles Rust with `--locked` and uses
`npm ci --include=dev --ignore-scripts` for the complete locked npm development
graph. Node and Python remain runtime prerequisites. Third-party dependencies
retain their own licenses; see [notices](../NOTICE.md) and
[source distribution instructions](distribution.md) for exact toolchain pins,
archive checksums, installation, and whole-prefix relocation.

A manually requested candidate can carry source provenance, a lockfile-derived
SPDX 2.3 inventory, and evidence bound to its archive SHA-256 and full source
commit. Cargo licenses are unknown from Cargo.lock; npm license strings are
unverified declarations. An attestation establishes source production provenance,
not installation correctness, binary provenance, license clearance, or release
approval.

Release status is **blocked** until the separate R3 gate builds and installs the
exact archive, verifies a fresh installation and relocation with external-project
fresh/persistent smoke tests, and records successful evidence bound to that
archive's SHA-256. Source-format checks and mocked installer tests do not satisfy
that gate. No publishing or stable promotion is implemented here.
