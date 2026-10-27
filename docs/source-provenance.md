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

`actions/attest-build-provenance` attests only the archive and the two JSON
sidecars. No compiled binaries are built or attested. The job grants only
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

## Parent integration still required: R3

This workflow intentionally does not call an invented validation script API. The
parent must integrate the separate R3 implementation against the **same uploaded
archive bytes**, bind its successful evidence to the inner archive SHA-256, and
require the full exact-artifact build/install/relocation validation before any
release attachment or publication. Download by immutable artifact ID and verify
the digests/provenance before consuming it. Do not rebuild another archive and
reuse this candidate's results. R3 should run without signing credentials when
executing extracted candidate build/test code. Keep this source-only evidence's
`NOT_RUN` result intact; attach separate R3 evidence rather than rewriting an
attested statement. Source provenance and R3 success are both prerequisites,
followed by explicit release review. No release publication path exists here.

## Action pin evidence

Checked against official upstream Git refs and action definitions on 2026-10-27:

- [checkout v7.0.1](https://github.com/actions/checkout/tree/3d3c42e5aac5ba805825da76410c181273ba90b1):
  `3d3c42e5aac5ba805825da76410c181273ba90b1`.
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
