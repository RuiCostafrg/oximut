# Manual source candidate provenance gate

This gate produces candidate artifacts only. It never creates tags, GitHub
releases/drafts, npm/crates publications, or stable promotions. The workflow must
first be reviewed and merged to the repository's trusted default branch. Protect
that branch and its workflow/scripts through the repository's normal review
policy. This gate does not configure repository protection rules.

Dispatch **Source candidate provenance (not release approval)** on the default
branch with `revision` set to the full lowercase 40-character candidate commit
SHA. Other workflow refs are skipped before the signing job runs. The job checks
out `github.workflow_sha`, requires it to equal the dispatch SHA, and requires the
candidate to be an ancestor of that trusted workflow commit. Candidate code is
read from Git objects as data; scripts from the candidate revision are never
executed. This permits an older reviewed candidate with a newer trusted builder;
both revisions and both script digests are recorded. Only `1.0.0-rc.1` is accepted,
and the builder checks all committed package versions against it.

The trusted builder creates the archive. The metadata script independently
rebuilds it in the same environment and compares the compressed archive SHA-256
before producing external sidecars:

- `oximut-1.0.0-rc.1-source.tar.gz` and its `.sha256` file;
- `.tar.gz.spdx.json`: SPDX 2.3 package inventory from the full root npm/Cargo
  locks, with no registry requests or license guesses;
- `.tar.gz.evidence.json`: candidate/workflow commits, script/archive/inventory/
  lockfile digests, source-format result, and explicit pending R3/release status;
- `provenance.sigstore.json`: verification bundle for source and metadata subjects.

The SPDX inventory retains every npm lock location, including nested duplicate
versions, development, optional, and platform entries, plus every workspace Cargo
lock entry. It supplies npm/Cargo package URLs where supported, raw npm integrity
and license evidence in package comments, and Cargo SHA-256 checksums. License
fields remain `NOASSERTION`; missing data stays missing. It does not assert a
resolved dependency-edge graph, installed-platform completeness, dependency
license clearance, or vendored third-party notices. See [NOTICE.md](../NOTICE.md).

`actions/attest-build-provenance` attests the archive, the two source metadata JSON
sidecars, and the successful R3 evidence JSON. No compiled binaries are built or attested. The job grants only
`contents: read`, `id-token: write`, and `attestations: write`; storage-record
creation is disabled, so artifact-metadata permission is unnecessary. The
attestation service may require Enterprise Cloud for private repositories. The
Actions artifact has a unique run/attempt name, no overwrite, 30-day retention,
and an immutable artifact ID/ZIP digest recorded in the run summary. The inner
archive SHA-256 is a separate identity from the upload ZIP digest. Retention
expiry/deletion still removes an artifact; preserve reviewed evidence before it
expires. Attestations and Actions uploads do not create a GitHub Release.

## Local format check

Use a clean checkout of the trusted workflow commit and an external output path:

```sh
origin=$(git rev-parse HEAD)
candidate=$origin # or an explicit reviewed ancestor SHA
python3 scripts/build-source.py --revision "$candidate" --version 1.0.0-rc.1 \
  --output-dir /tmp/oximut-source-provenance
python3 scripts/source-release-metadata.py --revision "$candidate" \
  --workflow-commit "$origin" \
  --archive /tmp/oximut-source-provenance/oximut-1.0.0-rc.1-source.tar.gz
python3 -m unittest discover -s tests -p 'test_source_release_metadata.py' -v
```

No local signature is generated. Consumers should verify the inner checksum and
GitHub attestation against the expected repository and signer workflow identity,
then compare the attested evidence's candidate SHA and workflow SHA with the
reviewed commits. The signed workflow identity is the trusted builder origin;
when candidate and workflow commits differ, the evidence sidecar binds the
candidate. For example, with the GitHub CLI:

```sh
gh attestation verify oximut-1.0.0-rc.1-source.tar.gz \
  --repo OWNER/REPO --signer-workflow OWNER/REPO/.github/workflows/source-candidate.yml
gh attestation verify oximut-1.0.0-rc.1-source.tar.gz.evidence.json \
  --repo OWNER/REPO --signer-workflow OWNER/REPO/.github/workflows/source-candidate.yml
```

## Exact artifact validation before attestation

The workflow uploads the source once, then a separate job downloads it by immutable
artifact ID with digest mismatch treated as an error. That job runs the trusted
`scripts/validate-source.py` against the same archive and expected candidate tree.
It compiles/installs once, removes extracted source, validates fresh/persistent
execution and cache behavior, and repeats after relocation. This job has read-only
repository permission and no signing credentials.

A final signing job downloads the original source artifact and successful R3
evidence by their immutable IDs. `scripts/check-source-evidence.py` verifies the
archive SHA-256, candidate commit, version, one-build record, source removal,
binary digest, and both successful lifecycle phases before attestation. No
candidate build code executes in that job. The earlier source-format sidecar's
`NOT_RUN` value remains an accurate point-in-time record; the separately attested
`r3-validation.evidence.json` supplies subsequent evidence for those exact bytes.
A failed validation blocks signing. No release publication path exists here;
success still requires explicit release review before any attachment/promotion.

### Candidate execution boundary

On native Ubuntu 24.04, `candidate-sandbox.py` requires bubblewrap user, mount,
PID, IPC, network and UTS namespaces. CI provisions Ubuntu's archive bubblewrap (0.9.0 on Noble) using
`scripts/setup-candidate-sandbox.sh`. Ubuntu 24.04's default AppArmor userns
policy denies namespace capabilities, which can fail loopback setup with
`Failed RTM_NEWADDR: Operation not permitted`. Following the
[Ubuntu release notes](https://documentation.ubuntu.com/release-notes/24.04/#unprivileged-user-namespace-restrictions),
the setup loads an executable-scoped `/usr/bin/bwrap` profile with `userns`
permission and `flags=(unconfined)`. This profile permits bubblewrap's own
sandbox construction; it is not an additional candidate confinement policy.
The global AppArmor/userns restrictions remain enabled. Only provisioning uses
sudo; the harness rejects host UID 0 and never launches candidates through sudo.
Explicit `--unshare-user --disable-userns` requires a user namespace and prevents
candidates creating nested user namespaces to regain namespace capabilities.
Missing profile support or namespace permissions fail the required native tests.

Installation can write only extracted source and
its new prefix. Each installed/relocated invocation gets a writable disposable
project and a read-only prefix; trusted harness, schema, validator dependencies,
archived reports and evidence are not mounted. Writable private tmpfs scratch
can contain shadow files at those pathnames; it does not expose or modify the
host originals. Host-side regression assertions check original bytes after
namespace teardown. The validator uses the trusted
checkout's locked npm dependencies, never candidate-installed JavaScript.
Only system tools, CA certificates and standalone Node/Rust toolchains are
mounted read-only. The environment is cleared; host homes, checkout credentials,
runner tokens, `/run`, and Docker sockets are absent. Capabilities are dropped
and bubblewrap sets no-new-privileges, including for setuid tools.

Installation retains network access for locked Cargo/npm downloads. Runtime
smokes use a separate network namespace. This is a filesystem/process isolation
boundary, not an installation egress firewall or protection against kernel
vulnerabilities. Run only on the dedicated, disposable GitHub-hosted validation
runner, never a credential-bearing development machine or shared host service.

Bubblewrap's PID namespace init exits before successful command return, killing
and reaping remaining descendants, including detached sessions. Timeouts kill
the process group and fail validation; output is never accepted on timeout.
Only after successful teardown does trusted code inspect prefix links/types,
read bounded regular report files without following links, copy reports, validate
schemas, or hash the exact binary. Prefix files remain read-only for both smoke
phases. File size, descriptor and process-count limits supplement command and
job timeouts; they are not a total disk or memory quota. Failed evidence remains
failed, and signing rejects evidence without the new isolation identity.

CI requires real hostile installer/runtime tests, read-only asset enforcement,
detached-process cleanup, failure/timeout checks, and the ordinary installed and
relocated smoke using the built CLI. A missing/unusable sandbox fails CI rather
than skipping. PRoot cannot enforce these mounts and is explicitly rejected.
Local work in PRoot validates format/lifecycle unit tests only; **native Ubuntu
CI must pass before this boundary is considered validated**. No attestation or
release dispatch is needed for the PR regression job.

## Action pin evidence

Checked against official upstream Git refs and action definitions on 2026-10-27:

- [checkout v7.0.1](https://github.com/actions/checkout/tree/3d3c42e5aac5ba805825da76410c181273ba90b1):
  `3d3c42e5aac5ba805825da76410c181273ba90b1`.
- [download-artifact v8.0.2](https://github.com/actions/download-artifact/tree/9000827ccba6bdab643e8b6fd33ac0654aef8333):
  `9000827ccba6bdab643e8b6fd33ac0654aef8333`; digest mismatch fails the download.
- [upload-artifact v7.0.2](https://github.com/actions/upload-artifact/tree/cf430e030ddbb5b0abf93d22962f4752f3646cd9):
  `cf430e030ddbb5b0abf93d22962f4752f3646cd9`.
- [attest-build-provenance v4.2.2](https://github.com/actions/attest-build-provenance/tree/4d101475d8b20a2381f78447822ac1eab6504dd8):
  `4d101475d8b20a2381f78447822ac1eab6504dd8`. Its composite action pins
  `actions/attest` at `508db95dd578ae2727ebd6217d5ba78e4fbda05d` (v4.2.1).

The current [official provenance action documentation](https://github.com/actions/attest-build-provenance)
recommends `actions/attest` for new workflows; the requested named
`attest-build-provenance` action is retained here as its pinned wrapper.
[Official attestation usage](https://github.com/actions/attest/blob/508db95dd578ae2727ebd6217d5ba78e4fbda05d/README.md)
documents permission scopes and provenance subjects.
