# Candidate source distribution

The initial distribution format for **1.0.0-rc.1** is a versioned source archive
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
  --version 1.0.0-rc.1 \
  --output-dir /tmp/oximut-source-candidate
cd /tmp/oximut-source-candidate
sha256sum -c oximut-1.0.0-rc.1-source.tar.gz.sha256
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
tar -xzf oximut-1.0.0-rc.1-source.tar.gz
cd oximut-1.0.0-rc.1
sha256sum -c SOURCE-DISTRIBUTION/SHA256SUMS
python3 scripts/install-source.py --prefix "$HOME/oximut-rc1"
python3 scripts/smoke-install.py --prefix "$HOME/oximut-rc1"
```

The existing installer uses `cargo build --locked --release` and a fresh
`npm ci --include=dev --ignore-scripts` installation. Full locked development
dependencies are intentional until a separate runtime package is designed.
See [installation details](install.md) for deadlines and runtime project setup.

To reinstall, run the installer into a new empty prefix. It refuses to overwrite
an existing installation. Move the **whole prefix** together to relocate it:

```sh
mv "$HOME/oximut-rc1" "$HOME/oximut-rc1-relocated"
python3 scripts/smoke-install.py --prefix "$HOME/oximut-rc1-relocated"
export PATH="$HOME/oximut-rc1-relocated/bin:$PATH"
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
source, test and config changes. Each run must pass its baseline, complete mutation
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
