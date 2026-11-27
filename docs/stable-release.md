# Stable 1.0.0 release

The initial stable distribution targets Ubuntu 24.04 x86-64. The
[compatibility contract](compatibility.md) defines the pinned prerequisites.
Review the [release checklist](maintenance.md) against the final revision.

1. Merge the release changes through reviewed PRs. Prepare the annotated tag
   message with the stable release notes and use the same version in Cargo,
   the npm workspace manifests, and both lockfiles.
2. Run correctness CI and Outcome parity on the exact `main` commit. Run the
   Stable release workflow manually on that commit to validate the candidate
   archive and installed prefix. Record the run URLs and results before tagging.
3. Push the annotated `v1.0.0` tag for the reviewed commit. The tag workflow
   confirms the matching green runs on `main`, builds the source archive, and
   installs and relocates it once on native Ubuntu. It checks the binary digest
   against the relocation evidence and uploads those validated bytes.
4. The publish job checks the uploaded bytes, attests the source and prefix, and
   publishes those artifacts to the existing tag. The GitHub Release contains
   the checksum manifest, dependency inventory, validation evidence, and
   provenance bundle. Branch and PR runs perform qualification only.
5. Verify the attached `oximut.rb` against the same release workflow, tag, and
   commit, then open its update PR in the public Homebrew tap. Run the formula's
   fresh and persistent installation smoke on Ubuntu 24.04 x86-64 before merging.
6. Publish the attested `oximut-1.0.0.tgz` to npm. For a newly registered
   package, use an authenticated maintainer session for the first publication
   and configure npm's trusted publisher for `npm-publish.yml` with the
   `release` environment afterward. When the trusted publisher is configured,
   the Npm publication workflow verifies the release attestation and stages
   the tarball for approval on npm.

The stable prefix is `validated-prefix.tar.gz`. It uses the same layout as the
RC prefix and carries the launcher, executable, adapter, locked dependencies,
and native zygote addon. Its build provenance is signed by
`.github/workflows/release.yml` at the exact release tag.
The [npm package](npm-packaging.md) is smoke-tested from the same prefix.
