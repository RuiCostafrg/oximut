# Install from source

Oximut is currently distributed as a source checkout. There is no supported
public npm or crates.io installation yet. The workspace packages remain private.
The `oximut` npm name returned HTTP 404 on 2026-10-26; this is an availability
observation, not a reservation or a published package.

The supported environment is Linux with Rust **1.96.0**, Node **26.3.1**, npm
**11.16.0**, and Python 3. Use the checkout's `rust-toolchain.toml`, `.node-version`,
and `package.json` pins. Node and Python must also be on PATH when running the
installed command. A source build needs network access to the Cargo and npm
registries, unless their caches already contain every locked dependency.

From the checkout:

```sh
python3 scripts/install-source.py --prefix "$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"
oximut --help
```

The installer runs a release Cargo build with `--locked`, then installs the
committed npm lockfile into a temporary staging directory using `npm ci
--include=dev --ignore-scripts`. It copies the native executable, adapter sources,
and npm dependencies into `PREFIX/lib/oximut`, then installs a Python launcher
at `PREFIX/bin/oximut`. The launcher supplies the installed fresh or persistent
adapter, while preserving an explicit `--adapter` argument. The complete prefix
can be moved together; the original checkout is not required at runtime.

The npm bundle currently includes developer tools from the root lockfile as well
as Vitest. This deliberately uses the existing tested dependency graph; a smaller
standalone runtime package is future release work. Dependency lifecycle scripts
are disabled. Node, Python, Rust, and project dependencies are not bundled; Rust
is needed only during installation. Your target project must provide its own
resolvable test imports and a working Vitest configuration. Oximut does not
install or modify that project's dependencies.

`cargo install --path crates/cli` alone does **not** produce this installation:
the native executable's development default refers to the build checkout's
adapter. Use the launcher above, or pass an explicit adapter with its dependencies.

The installer refuses to overwrite an existing `PREFIX/bin/oximut` or
`PREFIX/lib/oximut`. To update, install into a new prefix and switch your PATH.
To uninstall, remove those two paths. It does not edit shell startup files.

For a complete disposable installation and external-project smoke test:

```sh
prefix="$(mktemp -d)/oximut"
python3 scripts/install-source.py --prefix "$prefix"
python3 scripts/smoke-install.py --prefix "$prefix"
```

The smoke runs baseline and mutation testing in fresh and persistent modes from
a temporary directory outside the checkout. Its isolated example project links
to the bundle's Vitest dependency for test imports. It requires at least one
killed mutant and a complete report in each mode, and records the actual Node
runner entrypoints to verify they come from the installed prefix. The prefix remains available
for inspection; remove its temporary parent directory when finished.

The repository uses the MIT license in [LICENSE](../LICENSE). Bundled dependencies
retain their own licenses in the installed dependency tree. Publishing packages,
release archives, and binary provenance are not part of this source installer.
