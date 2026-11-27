import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/npm-packages.py"
SPEC = importlib.util.spec_from_file_location("npm_packages", SCRIPT)
NPM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NPM)
VERSION = "1.0.0"
REVISION = "a" * 40
TREE = "b" * 40
ELF_X64 = b"\x7fELF\x02\x01\x01" + bytes(11) + b"\x3e\x00"


class NpmPackageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = {
            NPM.LAUNCHER: b"#!/usr/bin/env python3\n",
            NPM.BINARY: ELF_X64,
            NPM.ADDON: ELF_X64,
            NPM.LICENSE: b"license text\n",
            f"{NPM.BUNDLE}/package.json": json.dumps({
                "name": "oximut", "version": VERSION, "engines": {"node": "26.3.1"},
            }).encode(),
            f"{NPM.BUNDLE}/package-lock.json": b"{}\n",
            f"{NPM.BUNDLE}/node_modules/vitest/package.json": b"{}\n",
            f"{NPM.BUNDLE}/node_modules/vitest/vitest.mjs": b"export {};\n",
            f"{NPM.BUNDLE}/packages/vitest-adapter/m1-runner.mjs": b"export {};\n",
            f"{NPM.BUNDLE}/packages/vitest-adapter/m2-worker.ts": b"export {};\n",
        }
        self.alias = f"{NPM.BUNDLE}/node_modules/.bin/vitest"
        self.evidence = {
            "gate": "R3", "status": "passed", "version": VERSION,
            "source": {"commit": REVISION, "tree": TREE}, "buildCount": 1,
            "isolation": "bubblewrap-pid-mount-v1", "extractedSourceRemoved": True,
            "tests": [{"phase": phase, "passed": True} for phase in ("installed", "relocated")],
            "binarySha256": hashlib.sha256(ELF_X64).hexdigest(),
            "archiveSha256": "d" * 64,
            "host": {"system": "Linux", "machine": "x86_64",
                     "platform": "Linux-azure-x86_64-with-glibc2.39"},
            "toolVersions": {"node": "v26.3.1", "npm": "11.16.0"},
        }

    def write_inputs(self, *, alias_target="../vitest/vitest.mjs", extra=None, trailing=b""):
        aliases = {self.alias: alias_target}
        with tarfile.open(self.root / NPM.ARCHIVE, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
            names = {Path(NPM.PREFIX) / name for name in self.files.keys() | aliases.keys()}
            for directory in sorted({p for name in names for p in name.parents} - {Path(".")}):
                entry = tarfile.TarInfo(directory.as_posix())
                entry.type, entry.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(entry)
            for name, contents in self.files.items():
                entry = tarfile.TarInfo(f"{NPM.PREFIX}/{name}")
                entry.mode = 0o755 if name in (NPM.LAUNCHER, NPM.BINARY) else 0o644
                entry.size = len(contents)
                archive.addfile(entry, io.BytesIO(contents))
            for name, target in aliases.items():
                entry = tarfile.TarInfo(f"{NPM.PREFIX}/{name}")
                entry.type, entry.linkname = tarfile.SYMTYPE, target
                archive.addfile(entry)
            if extra:
                archive.addfile(extra)
        if trailing:
            with (self.root / NPM.ARCHIVE).open("ab") as stream:
                stream.write(gzip.compress(trailing))
        digest = hashlib.sha256((self.root / NPM.ARCHIVE).read_bytes()).hexdigest()
        (self.root / f"{NPM.ARCHIVE}.sha256").write_text(f"{digest}  {NPM.ARCHIVE}\n")
        (self.root / "evidence.json").write_text(json.dumps(self.evidence))
        return digest

    def build(self, directory="package"):
        return NPM.build(self.root, self.root / "evidence.json", VERSION, REVISION, TREE,
                         self.root / directory)

    def test_package_retains_all_regular_files_and_npm_uses_only_the_oximut_bin(self):
        prefix_digest = self.write_inputs()
        first = self.build()
        self.assertEqual(first.read_bytes(), self.build("second").read_bytes())
        with tarfile.open(first) as archive:
            self.assertEqual(set(archive.getnames()), {
                f"package/{name}" for name in self.files.keys()
                | {"package.json", "BUILD-PROVENANCE.json", "README.md", "LICENSE",
                   NPM.NPM_LAUNCHER, NPM.TS_PRELOAD}
            })
            for name, contents in self.files.items():
                self.assertEqual(archive.extractfile(f"package/{name}").read(), contents)
            manifest = json.load(archive.extractfile("package/package.json"))
            self.assertEqual(manifest["bin"], {"oximut": NPM.NPM_LAUNCHER})
            self.assertEqual((manifest["name"], manifest["version"]), ("oximut", VERSION))
            self.assertEqual((manifest["os"], manifest["cpu"], manifest["libc"]),
                             (["linux"], ["x64"], ["glibc"]))
            self.assertTrue({"scripts", "dependencies", "optionalDependencies", "devDependencies",
                             "bundleDependencies"}.isdisjoint(manifest))
            provenance = json.load(archive.extractfile("package/BUILD-PROVENANCE.json"))
            self.assertEqual(provenance["prefixSha256"], prefix_digest)
            self.assertEqual(provenance["binarySha256"], self.evidence["binarySha256"])
        with self.assertRaisesRegex(ValueError, "output already exists"):
            self.build()

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "requires Node and npm")
    def test_npm_command_loads_bundled_typescript_only_and_preserves_node_options(self):
        node = subprocess.check_output(["node", "--version"], text=True).strip()
        if node != self.evidence["toolVersions"]["node"]:
            self.skipTest("requires the npm package's pinned Node version")
        worker = f"{NPM.BUNDLE}/packages/vitest-adapter/m2-worker.ts"
        dependency = f"{NPM.BUNDLE}/node_modules/vitest/example.ts"
        module = b"const value: number = globalThis.base + 2; console.log(value);\n"
        self.files[worker] = self.files[dependency] = module
        self.files[NPM.LAUNCHER] = (
            b"#!/usr/bin/env python3\n"
            b"import os, sys\nfrom pathlib import Path\n"
            b"prefix = Path(__file__).resolve().parent.parent\n"
            b"os.execvp('node', ['node', str(prefix / sys.argv[1])])\n"
        )
        self.write_inputs()
        tarball = self.build()
        project = self.root / "npm consumer"
        project.mkdir()
        (project / "package.json").write_text('{"private": true}')
        install = subprocess.run([
            "npm", "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund",
            "--force", "--cache", str(self.root / "cache"), str(tarball),
        ], cwd=project, capture_output=True, text=True, timeout=60)
        self.assertEqual(install.returncode, 0, install.stderr)

        commands = project / "node_modules"
        env = {**os.environ, "NODE_OPTIONS": "--import=data:text/javascript,globalThis.base%3D40"}
        original = subprocess.run([commands / "oximut" / NPM.LAUNCHER, worker], cwd=project,
                                  env=env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(original.returncode, 0)
        self.assertIn("ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING", original.stderr)

        linked = commands / ".bin/oximut"
        bundled = subprocess.run([linked, worker], cwd=project, env=env, capture_output=True,
                                 text=True, timeout=15)
        self.assertEqual(bundled.returncode, 0, bundled.stderr)
        self.assertEqual(bundled.stdout.strip(), "42")

        unrelated = subprocess.run([linked, dependency], cwd=project, env=env, capture_output=True,
                                   text=True, timeout=15)
        self.assertNotEqual(unrelated.returncode, 0)
        self.assertIn("ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING", unrelated.stderr)

    def test_rejects_wrong_asset_checksum_and_binary_hash(self):
        self.evidence["binarySha256"] = "0" * 64
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "binary does not match"):
            self.build()
        (self.root / f"{NPM.ARCHIVE}.sha256").write_text("wrong\n")
        with self.assertRaisesRegex(ValueError, "prefix checksum mismatch"):
            self.build()
        self.assertFalse((self.root / "package").exists())

    def test_rejects_unqualified_evidence(self):
        self.evidence["host"]["machine"] = "aarch64"
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "Ubuntu 24.04 x86-64"):
            self.build()
        self.evidence["host"]["machine"] = "x86_64"
        self.evidence["source"]["tree"] = "c" * 40
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "requested version, commit and tree"):
            self.build()

    def test_rejects_unsafe_paths_and_links(self):
        self.write_inputs(alias_target="../../oximut")
        with self.assertRaisesRegex(ValueError, "unsafe archive path"):
            self.build()
        for name, kind, error in (
            (f"{NPM.PREFIX}/../package.json", tarfile.REGTYPE, "unsafe archive path"),
            (f"{NPM.PREFIX}/{NPM.BINARY}", tarfile.REGTYPE, "duplicate prefix member"),
            (f"{NPM.PREFIX}/{NPM.BUNDLE}/fifo", tarfile.FIFOTYPE, "unsupported prefix member"),
            (f"{NPM.PREFIX}/{NPM.BUNDLE}/hardlink", tarfile.LNKTYPE, "unsupported prefix member"),
        ):
            with self.subTest(name=name):
                entry = tarfile.TarInfo(name)
                entry.type = kind
                self.write_inputs(extra=entry)
                with self.assertRaisesRegex(ValueError, error):
                    self.build()

    def test_rejects_excess_expansion_and_trailing_tar_data(self):
        self.write_inputs(trailing=b"trailing payload")
        with self.assertRaisesRegex(ValueError, "nonzero trailing prefix data"):
            self.build()
        with patch.object(NPM, "MAX_EXPANDED", 1024):
            with self.assertRaisesRegex(ValueError, "expanded prefix limit exceeded"):
                self.build()
