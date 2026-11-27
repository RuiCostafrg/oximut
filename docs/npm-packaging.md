# Build the Oximut npm tarball

Use the `validated-prefix.tar.gz`, its `.sha256` sidecar, and the passing
`evidence.json` from the same trusted Ubuntu 24.04 x86-64 R3 binary validation
run. Use the expected revision and tree of that run. For an RC release with
separate source provenance and binary validation runs, use the commit of the
binary validation run and its evidence.

On the native Ubuntu 24.04 x86-64 runner, set up the Node and npm versions
recorded in the R3 evidence and the same bubblewrap setup as the source gate.

```sh
bash scripts/setup-candidate-sandbox.sh
python3 scripts/npm-packages.py \
  --assets "$prefix_assets" --evidence "$binary_evidence" \
  --version "$version" --revision "$binary_revision" --tree "$tree" \
  --output "$output" --smoke
```

The script writes `oximut-VERSION.tgz` in a new output directory. It verifies
the prefix checksum, version and binary hash against the input records, and
compares the tarball's file inventory, bytes, and normalized modes with the
prefix. `BUILD-PROVENANCE.json` records the source archive, prefix, and binary
digests. The trusted R3 artifact supplies the prefix and its checksum together.

The tarball retains the runtime under `lib/oximut`, including the full locked
dependency tree and native addon. It omits the internal npm-generated
`node_modules/.bin` symlinks, which npm drops during extraction and the
Oximut runtime does not use. The root manifest has no dependencies or lifecycle
hooks. npm links the existing Python launcher as the `oximut` command. Use the
generated tarball directly to preserve its verified inventory and modes.

With `--smoke`, the script installs the tarball offline in a fresh directory
outside the checkout, verifies the installed inventory and executable bits,
and checks the npm-linked command's version. The existing mutation smoke
exercises the packaged fresh and persistent adapters, including actual
zygote forks. Release packaging should include this native smoke. Omitting the
flag is useful for inspecting package contents on a different build host.
