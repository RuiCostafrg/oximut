# Security policy

## Reporting a vulnerability

GitHub private vulnerability reporting is currently disabled for this repository
(verified on 2026-10-27). Please do not post vulnerability details, exploit
steps, credentials, tokens, or other secrets in a public issue or discussion.
Until private reporting is enabled, use a private channel already established
with the repository owner. This document does not publish or imply a new email
address or other reporting endpoint.

If no such private channel is available, do not include sensitive details in a
public report. You may ask publicly for a private way to contact the owner, but
keep the vulnerability details private until one is established.

## Scope and support

The supported configuration, when a release is made, is limited to the exact
contract in [the compatibility matrix](docs/compatibility.md). The current
source-only `1.0.0-rc.1` is a release candidate, not a stable release. Its
acceptance is pending; no binary target matrix is claimed. See the
[release checklist](docs/maintenance.md) for pending gates and evidence.

There is no security response SLA or guaranteed remediation timeline. No
release or support commitment should be inferred from this policy.

## Dependency updates

Pinned dependency versions and update automation are described in the
[compatibility matrix](docs/compatibility.md) and [README](README.md). Security
updates are evaluated against the exact supported configuration and its checks.
An update may require a release candidate and compatibility evidence before it
is treated as supported. This policy does not promise a particular update
cadence.
