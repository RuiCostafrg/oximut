# Source candidate notices

Oximut's own source is licensed under the MIT License in [LICENSE](LICENSE),
including its copyright and permission notice. This does not relicense third-party
dependencies or establish that the complete installation is MIT-only.

The 1.0.0-rc.1 source archive contains the full root `package-lock.json` and
workspace `Cargo.lock`, including development, optional, and platform-specific
locked entries. Runtime dependency source, npm tarballs, Cargo crates, `node_modules`,
compiled executables, and their license texts are not vendored into the archive.
Installation fetches dependencies under their respective licenses. The npm
installation intentionally includes the full development graph.

The external `.spdx.json` sidecar inventories those lockfiles without installing
or downloading dependencies. npm lockfile license declarations and integrity
strings are preserved verbatim in package comments. SPDX license fields are
`NOASSERTION`: these declarations have not been independently verified. Cargo.lock
provides identities and checksums but no license declarations; Cargo licenses
remain unknown. No licenses are inferred from package names or registry URLs.
The inventory is not a license clearance report, vulnerability audit, complete
third-party notice collection, or inventory of an installed platform's bytes.
Review exact dependency license texts and required notices before redistributing
a built installation or vendoring dependencies. These materials are not supplied
by this source-only candidate gate.

## Vendored benchmark corpus

The real-project benchmark includes unmodified colord production source and test
suites from upstream commit `1404cfe80cf8fe6df1a876ea3b12aa8eccc7feff` (2.10.0).
This corpus is MIT-licensed by its upstream authors; its copyright and permission
notice are preserved in [the corpus license](benchmarks/real-project/colord/LICENSE.md).
See [provenance](benchmarks/real-project/colord/PROVENANCE.md) for file hashes and
harness adaptations. It is benchmark input, not an Oximut runtime dependency,
and is outside the lockfile-derived dependency inventory.
