# Release maintenance and readiness

This page records the maintenance policy and release gate status. The detailed
runtime contract remains in [compatibility.md](compatibility.md); milestone
behavior is documented in [M1–M6](m1.md), and the rationale and full acceptance
criteria are in the [release roadmap](release-roadmap.md). This checklist is a
status record, not proof that a gate has passed.

## Candidate status

The source-only `1.0.0-rc.1` candidate is being prepared. Candidate acceptance
is pending. It has no published binaries and no binary target matrix. No item
below should be treated as complete without a recorded result tied to the
candidate revision.

## Release checklist

Each gate starts **Pending** until its acceptance evidence is recorded for the
exact release candidate. A checked-in document or an earlier milestone result
does not by itself satisfy a candidate gate.

| Gate | Status | Evidence required |
| --- | --- | --- |
| Supported contract | Pending | Review [compatibility.md](compatibility.md), CLI/report behavior, cache semantics, installer instructions, unsupported cases, and fallbacks as one contract. Record exact versions and each claimed host. |
| Correctness and failure handling | Pending | Run the release roadmap's R3 acceptance corpus on the candidate and final source distribution; archive revision, environment, commands, results, and any differences. |
| Source distribution | Pending | Build and test the exact source artifact in a clean external project after relocation, including documented install prerequisites and licensing notices. This candidate makes no binary-target claim. |
| Support and security | Pending | Confirm [SUPPORT.md](../SUPPORT.md) and [SECURITY.md](../SECURITY.md) match the actual support boundary and available reporting route; record security/update policy review. |
| Release notes and claims | Pending | State known limits and only claims supported by archived candidate evidence. Link the compatibility contract, installation instructions, and benchmark evidence where relevant. |

The detailed gate definitions and optional expansion evidence are maintained in
[release-roadmap.md](release-roadmap.md). When a gate is accepted, replace its
status with a dated evidence reference to the candidate revision and artifact;
do not mark it complete solely because its documentation exists.

## Supported update policy

The compatibility matrix is exact-version based. A dependency or runtime update
is not automatically supported just because upstream considers it compatible.
Evaluate changes against the repository's applicable checks and update the
matrix only when the new combination has evidence. Security updates are
considered against the same supported contract; this policy does not set a
response SLA or release cadence.

Users should use the repository's public issue tracker for reproducible,
non-sensitive defects. Security reports must remain private; see
[SECURITY.md](../SECURITY.md). Private vulnerability reporting was verified as
disabled on 2026-10-27. Until it is enabled, the security policy directs users
to an already established private channel with the repository owner and does
not invent a contact address.

## Compatibility and versioning

The compatibility matrix is the source of truth for the supported runtime and
tool versions. Add a version or platform only after its relevant checks have
run and the evidence is recorded. A change to the declared contract should be
called out in release notes.

Treat the CLI arguments and exit meanings, normal report fields and semantics,
JSON schema, and the separate `--list` output contract as public interfaces.
Follow the rules in [report-schema.md](report-schema.md#interface-versioning-policy)
for schema changes. Changes that remove or alter a documented behavior,
configuration, output meaning, or supported contract incompatibly require a
major version change. Additive compatible behavior may use a minor version;
fixes that preserve the contract may use a patch version. A change may still
need a major version when it breaks the documented contract even if the JSON
shape is unchanged. Explain any intentional compatibility decision in release
notes.

## Performance claims

The proposed performance goals are not release evidence and are not claimed as
achieved. In particular, no 50% memory reduction or 20% runtime reduction is
established. Any Oximut-versus-Stryker claim must compare equivalent mutation
scope and test inventories, pin the compared software and environment, disclose
exclusions and regressions, and link raw data and commands. Use the method in
[benchmarks.md](benchmarks.md); keep observed results separate from targets.
