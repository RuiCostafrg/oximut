# Support policy

Oximut's supported configuration is intentionally narrow. Consult
[Compatibility and support boundary](docs/compatibility.md) for the exact
Linux, Node, npm, Python, Rust, Oxc, Vitest, project, and instrumentation
constraints. A pinned dependency version alone does not establish support for
every project using it.

The `1.0.0-rc.1` candidate is source-only and pending acceptance. It does not
establish a stable release or a binary compatibility matrix. The current
release gates and their evidence status are tracked in the
[maintenance checklist](docs/maintenance.md). Until those gates are accepted,
do not interpret candidate status as a completed public support commitment.

## Defects and support expectations

Use the repository's public issue tracker for reproducible, non-sensitive
defects. Do not include credentials, private source, or vulnerability details
in public reports; see [SECURITY.md](SECURITY.md) for security reporting.
Include the Oximut revision/version, the exact supported tool versions, minimal
reproduction steps, and relevant output when possible. Reports outside the
documented configuration may still be useful, but are not covered by the
support boundary.

There is no support SLA, response-time guarantee, or promise that every report
will receive a fix. No maintainer availability or obligation is implied.

## Compatibility and updates

The initial supported contract is exact-version based. Changes to supported
runtime/tool versions or project features require explicit review and evidence
before they are added to the matrix. See the roadmap's
[compatibility expansion gates](docs/release-roadmap.md#optional-compatibility-and-performance-expansion).
Dependency updates are evaluated for the supported setup; no update cadence is
promised.

Public interfaces and breaking changes follow the versioning notes in
[docs/maintenance.md](docs/maintenance.md). Release claims must match archived
evidence. Performance goals are not results: any comparison must use equal
mutation scope and test inventory, report exclusions and regressions, and link
to reproducible measurements. The proposed 50% memory and 20% time targets are
not claimed as achieved; see the [benchmark plan](docs/benchmarks.md).
