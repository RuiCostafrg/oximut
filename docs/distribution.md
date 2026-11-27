# Candidate source distribution

The distribution format for **1.0.0-rc.3** (unchanged since rc.1) is a versioned source archive
intended for a GitHub Release attachment. This is a release candidate, not stable
1.0.0. The builder creates local artifacts only: it does not create a release,
push, tag, publish to npm/crates.io, or build downloadable binaries. GitHub's
automatic repository snapshots are not this curated source format.

The initial installation scope is Linux. No macOS, Windows, cross-compilation,
or prebuilt binary target support is claimed by this format. The source archive
is architecture independent; successful installation on a particular Linux
architecture still requires compiling and validating it there. Separate artifact
validation is required before attaching anything to a release.

## Build an archive

Prerequisites for the archive builder: Git and Python **3.11 or newer** (standard
library only). Start with a clean checkout, including no non-ignored untracked
files. Ignored dependency/build caches may remain; they are never read. The
version must match the selected commit's Cargo workspace, workspace Cargo.lock
entries, root npm manifest/lockfile, and adapter manifest. npm root dependency
and engine declarations must agree with the lockfile root entry.

```sh
revision="$(git rev-parse HEAD)"
python3 scripts/build-source.py \
  --revision "$revision" \
  --version 1.0.0-rc.3 \
  --output-dir /tmp/oximut-source-candidate
cd /tmp/oximut-source-candidate
sha256sum -c oximut-1.0.0-rc.3-source.tar.gz.sha256
```

Use a new output directory outside the checkout; existing artifacts are never
replaced. `--revision` is mandatory and resolves to a commit, including an earlier
commit if explicitly requested. All payload bytes and executable permissions
come from that commit's Git objects, not the current filesystem or untracked
files. `--repo PATH` selects another clean repository. Run a trusted builder:
the manifest records the running builder's SHA-256 independently of the payload
commit. The builder at that commit can differ when packaging an older revision.

The output is `oximut-VERSION-source.tar.gz` plus its SHA-256 sidecar. Extraction
creates one `oximut-VERSION/` directory. Archive entries are sorted, regular files
only; uid/gid, owner names, modes, tar timestamps (commit time), and gzip headers
are normalized. The same commit, version, builder, and Python/zlib environment
produce identical bytes regardless of checkout/output path or build time.
Compression bytes across different Python/zlib versions are not guaranteed.
Unsafe paths, links, submodules in the payload, and reserved metadata paths fail
the build. No local dependencies or native executables are bundled.

The archive retains source, workspace experiments, fixtures, validation tests,
benchmark harnesses, adapter sources, docs, and locked dependency manifests.
It excludes `.git`, `node_modules`, `target`, `dist`, coverage, private `.oximut`
inputs, adapter `.experiment-*` inputs, Stryker/Vitest scratch directories,
`.env*`, Python caches, logs, CI/review configuration, the internal plan, and
`benchmarks/results/` measurement archives. Historical benchmark results are not
needed for source compilation or tests and remain available in Git history.
Only committed files passing these exclusions can enter the payload.

`SOURCE-DISTRIBUTION/manifest.json` records the full source commit and tree IDs,
commit timestamp, candidate version, builder digest, pinned toolchain versions,
lockfile digests, npm installation command, and a sorted payload inventory with
sizes, modes, and SHA-256 hashes. `SOURCE-DISTRIBUTION/SHA256SUMS` covers every
payload file and the manifest; it omits itself to avoid a circular hash. The
external sidecar covers the entire compressed archive. These are reproducibility
and integrity records, not a signature, attestation, SBOM, dependency audit, or
claim that release validation has passed.

## Install and relocate

Installation requires Linux, Rust **1.96.0** (Cargo and a working native linker /
C toolchain), Node **26.3.1**, npm **11.16.0**, and Python **3.11 or newer** on PATH.
Cargo and npm registry access is needed unless all locked dependencies are cached.
The archive contains source and lockfiles, not vendored dependencies. Provide
sufficient disk space for the release build and full npm development dependency
graph. The source builder does not download or check these toolchains.

```sh
tar -xzf oximut-1.0.0-rc.3-source.tar.gz
cd oximut-1.0.0-rc.3
sha256sum -c SOURCE-DISTRIBUTION/SHA256SUMS
python3 scripts/install-source.py --prefix "$HOME/oximut-rc3"
python3 scripts/smoke-install.py --prefix "$HOME/oximut-rc3"          # add --require-zygote on glibc Linux
```

The existing installer uses `cargo build --locked --release` and a fresh
`npm ci --include=dev --ignore-scripts` installation. Full locked development
dependencies are intentional until a separate runtime package is designed.
See [installation details](install.md) for deadlines and runtime project setup.

To reinstall, run the installer into a new empty prefix. It refuses to overwrite
an existing installation. Move the **whole prefix** together to relocate it:

```sh
mv "$HOME/oximut-rc3" "$HOME/oximut-rc3-relocated"
python3 scripts/smoke-install.py --prefix "$HOME/oximut-rc3-relocated"
export PATH="$HOME/oximut-rc3-relocated/bin:$PATH"
oximut --help
```

Node and Python remain runtime prerequisites; the checkout/extracted archive and
Rust are no longer needed after installation. Run the external-project smoke on
the actual candidate before and after relocation; it checks both fresh and
persistent adapters. Unit tests of the source format do not substitute for that
artifact validation, which is a separate release gate.

Focused format and installer checks (no dependency install or performance runs):

```sh
python3 -m unittest discover -s tests -p 'test_distribution.py' -v
python3 -m unittest discover -s tests -p 'test_install.py' -v
```

For RC3 notes, third-party licensing scope, and the manual source
provenance gate, see [draft release notes](release-notes-1.0.0-rc.3.md),
[notices](../NOTICE.md), and [source provenance](source-provenance.md).
## R3 exact artifact gate

The R3 workflow (`.github/workflows/source-artifact.yml`) builds the selected
commit's source archive, validates those exact compressed bytes, then invokes the
archive's real release installer **once**. Its claimed native validation host is
**Ubuntu 24.04 Linux x86_64**. Linux arm64 in developer PRoot environments is
supplementary developer evidence only; it does not establish native arm64 support.
A passing CI run is required evidence, not something inferred from unit tests.

From a clean committed checkout, run the same gate manually:

```sh
revision=$(git rev-parse HEAD)
tree=$(git rev-parse 'HEAD^{tree}')
version=$(python3 -c 'import json; print(json.load(open("package.json"))["version"])')
candidate=$(mktemp -d /tmp/oximut-r3-candidate.XXXXXX)
python3 scripts/build-source.py --revision "$revision" --version "$version" --output-dir "$candidate"
python3 scripts/validate-source.py \
  --archive "$candidate/oximut-$version-source.tar.gz" \
  --version "$version" --revision "$revision" --tree "$tree" \
  --output-dir "$candidate/validation"
```

The validator is trusted checkout tooling; it does not execute validation code
from the archive. It requires the external sidecar, full expected commit and tree
IDs, canonical inventory and internal checksums, consistent package/lock/toolchain
metadata, and normalized file ownership, permissions and timestamps. It rejects
links, sparse files, extended tar headers, duplicates, unsafe paths, excluded
payload, extra metadata, and nonzero trailing tar data. Limits are 64 MiB compressed,
256 MiB decompressed (including headers/padding), 16 MiB per file and 20,000 files.
The entire archive is verified before manual extraction into a private directory.
The sidecar and manifest establish integrity and consistency, not authenticity;
obtain candidate checksums and expected revision IDs from a trusted source.

The gate removes the extracted source after installation. A trusted harness runs
from a separate temporary directory outside the checkout and creates an independent
project using only the installed dependencies. It checks the installed adapter
paths and runs the exact installed command in fresh and persistent modes. For each
mode it checks cold cache, warm reuse, forced execution, and invalidation after
source, test and config changes. Those runs pin `--workers 1`; one more persistent
run without `--workers` must report the automatic default (`workers=N (auto: ...)`),
and with `--require-zygote` still fork its mutant workers from the zygote. Each run must pass its baseline, complete mutation
execution, kill a mutant, preserve outcome totals, and pass the strict published
report schema. The gate repeats these checks after moving the **whole prefix**
between paths containing spaces, and verifies the native executable hash is unchanged.

`validation/evidence.json` records archive and installed binary SHA-256, source
revision/tree, host, tool versions, installation output and test results; reports
and cache diagnostics remain in `installed/` and `relocated/`. Failures after
artifact inspection produce failed evidence and a failing exit status. The final
`relocated prefix with spaces/` is retained for further checks. CI uploads the
source archive/sidecar, evidence/reports, and a checksummed tar of this exact prefix.
Later provenance work must reuse these assets and binary digest rather than build
a second candidate. Installation/build and this correctness gate run outside any
measured benchmark; these results make no performance claim.

Run the lightweight gate tests without Cargo/npm installation:

```sh
python3 -m unittest discover -s tests -p 'test_artifact_validation.py' -v
actionlint .github/workflows/source-artifact.yml
```

## Validated Linux x86_64 prefix (private Homebrew tap)

The source archive is the reviewable input. The **prebuilt prefix** that the
private Homebrew tap installs (`validated-prefix.tar.gz`, attached to the GitHub
pre-release) is a second artifact derived from it:

1. `source-artifact.yml` builds the source archive for the PR/commit, then
   `validate-source.py` installs it **once** on Ubuntu 24.04 x86_64 (Cargo
   build, `npm ci`, zygote addon compile), deletes the extracted source, and
   smokes the installed prefix, then the same prefix moved to a path with spaces.
2. The workflow tars the final `relocated prefix with spaces/` directory as
   `validated-prefix.tar.gz` with a `.sha256` sidecar, and uploads it with the
   source archive and `evidence.json` as the `source-candidate-linux-x86_64`
   Actions artifact.
3. `evidence.json` records `binarySha256`: the SHA-256 of
   `lib/oximut/oximut` in that exact prefix. The Homebrew formula pins both the
   tarball SHA-256 (its `sha256`) and this binary SHA-256, and refuses to install
   if the extracted binary differs. Nothing is rebuilt or re-installed by brew.

The prefix includes the compiled zygote addon
(`lib/oximut/packages/vitest-adapter/native/build/zygote-fork.node`). It is built
by `scripts/build-zygote-addon.py` against the Node 26.3.1 headers that the
runner used. It is a Node-API (`node_api.h`) addon, so it loads in any Node
26.3.1 build, including the private Node the formula installs. It needs glibc
2.34 or newer (the Oximut binary itself needs glibc 2.39). The R3 smoke runs
with `--require-zygote`: the prefix must contain the addon, every persistent run
that executes mutants must report `zygote=on`, and the pool's own counters must
show forked workers and no zygote failure. A requested-but-unloadable addon
would fall back to normal workers silently, which that check does not accept.

A host without the addon (musl/Alpine, macOS, a source install without a C
compiler) is still a working install: the installer prints
`Zygote addon skipped: ...`, the run reports `zygote=off`, and mutants run in
normal workers with identical outcomes. The musl fallback is exercised in CI by
the `Zygote workers` workflow.

Neither tarball nor evidence is a signed or attested binary: the attestation
workflow (`source-candidate.yml`) attests the source archive and its metadata
only.

## GitHub Action

Use the composite action on `ubuntu-24.04` x86-64 once a stable Oximut release
with an attested prefix is available. Specify an exact stable `version` such as
`1.0.0` or `v1.0.0`. The existing release candidates have unattested prefixes.
Use a full commit SHA for the action ref. For a separate pin on the artifact's
source and signing workflow, set `release-commit` to the full reviewed release
tag commit SHA.

Set up Node.js **26.3.1** and a current Python **3.13** patch release (minimum
**3.13.5**) on `PATH` before calling the action. GitHub's Ubuntu runner includes
`gh` with artifact attestation support (verified CLI interface: **2.95.0**).
The action uses the job's GitHub token by default. Use a read-only token with
access to the release assets and attestations if overriding `token`. The project
still needs its own test dependencies and a working Vitest configuration.

```yaml
jobs:
  mutation:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      attestations: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0
        with:
          node-version: '26.3.1'
      - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0
        with:
          python-version: '3.13'
      - run: npm ci
      - uses: RuiCostafrg/oximut@ACTION_COMMIT_SHA # Replace with the reviewed action commit.
        id: oximut
        with:
          version: '1.0.0'
      - run: oximut --source 'src/example file.ts' --report mutation-report.json
```

The action installs under the runner's temporary directory outside the project
checkout. The successful step exports `version`, `bin-dir`, and `executable`,
and adds `bin-dir` to `PATH` for subsequent steps. Rust and an Oximut dependency
install are unnecessary because the archive already includes the native binary,
launcher, adapters, and runtime dependencies.

To run immediately after installing, replace the last `run` step above with the
following inputs on the action. `working-directory` defaults to `.`. Each
nonempty line in `args` is one literal argument, including embedded spaces.
Oximut's exit status determines the step result.

```yaml
with:
  version: '1.0.0'
  run: 'true'
  args: |
    --source
    src/example file.ts
    --report
    mutation-report.json
```

The release must contain `validated-prefix.tar.gz` and its canonical
`validated-prefix.tar.gz.sha256` sidecar from the exact R3-validated Ubuntu
24.04 x86-64 prefix. The action checks the SHA-256 and verifies a GitHub-hosted
SLSA build provenance attestation registered in `RuiCostafrg/oximut`. It requires
the signed repository ID `1431290496`, the exact signing identity
`https://github.com/RuiCostafrg/oximut/.github/workflows/release.yml@refs/tags/vVERSION`,
the corresponding source tag, and a subject named `validated-prefix.tar.gz`
with the archive digest. With `release-commit`, it also verifies both signed
commit digests. Publish the exact prevalidated prefix with that attestation and
protect the version tag against changes.
