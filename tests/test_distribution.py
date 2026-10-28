"""Focused source-format tests: no Cargo/npm builds or network access."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("source_builder", ROOT / "scripts/build-source.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
VERSION = "1.0.0-rc.1"


class SourceDistribution(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        for name in set(paths) | BUILDER.REQUIRED:
            if not name or BUILDER.excluded(BUILDER.safe_path(name)):
                continue
            source = ROOT / name
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        self.git("init", "-q")
        self.commit()
        self.revision = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output([
            "git", "-c", "user.name=RuiCostafrg",
            "-c", "user.email=35194998+RuiCostafrg@users.noreply.github.com", *args],
            cwd=self.repo, stderr=subprocess.PIPE).decode()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def build(self, output="output", revision=None, version=VERSION):
        return BUILDER.build(self.repo, revision or self.revision, version, self.base / output)

    def contents(self, archive):
        with tarfile.open(archive) as tar:
            return {m.name.removeprefix(f"oximut-{VERSION}/"): (m, tar.extractfile(m).read())
                    for m in tar.getmembers()}

    def test_deterministic_inventory_checksums_and_payload(self):
        first = self.build()
        second = self.build("second")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.with_suffix(first.suffix + ".sha256").read_text(),
                         f"{hashlib.sha256(first.read_bytes()).hexdigest()}  {first.name}\n")
        files = self.contents(first)
        self.assertTrue(BUILDER.REQUIRED <= files.keys())
        self.assertIn("crates/cli/src/main.rs", files)
        self.assertIn("fixtures/pure/vitest.config.ts", files)
        self.assertIn("tests/test_install.py", files)
        self.assertIn("packages/vitest-adapter/test/m1.test.mjs", files)
        self.assertEqual(files["packages/vitest-adapter/protocol.mjs"][1],
                         (ROOT / "packages/vitest-adapter/protocol.mjs").read_bytes())
        manifest = json.loads(files["SOURCE-DISTRIBUTION/manifest.json"][1])
        self.assertEqual(manifest["source"]["commit"], self.revision)
        self.assertEqual(manifest["version"], VERSION)
        names = [entry["path"] for entry in manifest["inventory"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(set(names), files.keys() - {
            "SOURCE-DISTRIBUTION/manifest.json", "SOURCE-DISTRIBUTION/SHA256SUMS"})
        for entry in manifest["inventory"]:
            info, data = files[entry["path"]]
            self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(entry["size"], len(data))
            self.assertEqual(entry["mode"], f"{info.mode:04o}")
            self.assertEqual((info.uid, info.gid, info.uname, info.gname), (0, 0, "", ""))
        for line in files["SOURCE-DISTRIBUTION/SHA256SUMS"][1].decode().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256(files[name][1]).hexdigest())
        with self.assertRaisesRegex(ValueError, "existing artifact"):
            self.build()

    def test_extracted_archive_reinstall_and_prefix_relocation(self):
        # Exercise the archived real installer/launcher; toolchain phases are faked.
        extracted = self.base / "extracted"
        with tarfile.open(self.build()) as tar:
            tar.extractall(extracted, filter="data")
        source = extracted / f"oximut-{VERSION}"
        spec = importlib.util.spec_from_file_location("archived_installer", source / "scripts/install-source.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        executable = self.base / "fake-compiled-oximut"
        executable.write_text("#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
        executable.chmod(0o755)
        artifact = json.dumps({"reason": "compiler-artifact", "executable": str(executable),
                               "target": {"name": "oximut", "kind": ["bin"]}})
        first, second = self.base / "first prefix", self.base / "second prefix"
        for prefix in (first, second):
            with mock.patch.object(installer, "run_phase", side_effect=[
                    subprocess.CompletedProcess([], 0, artifact), subprocess.CompletedProcess([], 0)]):
                installer.install(prefix)
        with self.assertRaisesRegex(SystemExit, "replace existing"):
            installer.install(first)
        relocated = self.base / "relocated prefix"
        first.rename(relocated)
        shutil.rmtree(extracted)
        for prefix in (relocated, second):
            adapter = prefix / "lib/oximut/packages/vitest-adapter"
            self.assertEqual((adapter / "protocol.mjs").read_bytes(),
                             (ROOT / "packages/vitest-adapter/protocol.mjs").read_bytes())
            # No checkout, dependencies or TS stripping are available here. The
            # installed protocol and outer adapter must resolve after relocation.
            subprocess.run([
                "node", "--no-experimental-strip-types", "--input-type=module", "-e",
                f"const p = await import({json.dumps((adapter / 'protocol.mjs').as_uri())});"
                f"await import({json.dumps((adapter / 'index.mjs').as_uri())});"
                "try { p.validateRequest(null); throw new Error('accepted invalid request'); }"
                "catch (e) { if (e.message !== 'Unsupported or missing protocol (expected 1)') throw e; }",
            ], cwd=self.base, check=True, capture_output=True, text=True, timeout=10)
            for arguments, runner in (([], "m1"), (["--execution", "persistent"], "m2")):
                result = json.loads(subprocess.check_output(
                    [str(prefix / "bin/oximut"), *arguments], cwd=self.base))
                self.assertEqual(result, ["--adapter", str(
                    prefix / f"lib/oximut/packages/vitest-adapter/{runner}-runner.mjs"), *arguments])
                self.assertTrue(Path(result[1]).is_file())

    def test_missing_protocol_runtime_rejected(self):
        self.git("rm", "packages/vitest-adapter/protocol.mjs")
        self.commit()
        with self.assertRaisesRegex(ValueError, "missing required source files.*protocol.mjs"):
            self.build(revision=self.git("rev-parse", "HEAD").strip())

    def test_older_typescript_protocol_revision_remains_buildable(self):
        # Model the legacy layout: runtime lives in protocol.ts, not a shim.
        runtime = self.repo / "packages/vitest-adapter/protocol.mjs"
        (runtime.with_suffix(".ts")).write_bytes(runtime.read_bytes())
        for runner in ("m1-runner.mjs", "m2-runner.mjs"):
            path = self.repo / "packages/vitest-adapter" / runner
            path.write_text(path.read_text().replace("./protocol.mjs", "./protocol.ts"))
        self.git("rm", "packages/vitest-adapter/protocol.mjs")
        self.commit()
        old = self.git("rev-parse", "HEAD").strip()
        (self.repo / "README.md").write_text("newer checkout")
        self.commit()
        files = self.contents(self.build(revision=old))
        self.assertNotIn("packages/vitest-adapter/protocol.mjs", files)
        self.assertIn("packages/vitest-adapter/protocol.ts", files)

    def test_either_runner_import_requires_the_runtime_protocol(self):
        runtime = self.repo / "packages/vitest-adapter/protocol.mjs"
        data = runtime.read_bytes()
        for changed in ("m1-runner.mjs", "m2-runner.mjs"):
            with self.subTest(changed=changed):
                runtime.write_bytes(data)
                for runner in ("m1-runner.mjs", "m2-runner.mjs"):
                    path = self.repo / "packages/vitest-adapter" / runner
                    source = path.read_text().replace("./protocol.mjs", "./protocol.ts")
                    if runner == changed:
                        source = source.replace("./protocol.ts", "./protocol.mjs")
                    path.write_text(source)
                runtime.unlink()
                self.commit()
                with self.assertRaisesRegex(ValueError, "missing required source files.*protocol.mjs"):
                    self.build(revision=self.git("rev-parse", "HEAD").strip())

    def test_explicit_old_revision_not_current_checkout(self):
        original = (self.repo / "README.md").read_bytes()
        (self.repo / "README.md").write_text("different HEAD content")
        self.commit()
        self.assertEqual(self.contents(self.build())["README.md"][1], original)
        current = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.contents(self.build("head", current))["README.md"][1], b"different HEAD content")

    def test_dirty_and_untracked_inputs_rejected(self):
        path = self.repo / "README.md"
        original = path.read_bytes()
        path.write_text("dirty")
        with self.assertRaisesRegex(ValueError, "clean"):
            self.build()
        path.write_bytes(original)
        (self.repo / "local-secret").write_text("must never package")
        with self.assertRaisesRegex(ValueError, "clean"):
            self.build()
        self.assertFalse((self.base / "output").exists())

    def test_generated_tracked_and_ignored_files_excluded(self):
        names = ["benchmarks/results/huge.json", "node_modules/private.js", "target/private",
                 ".oximut/generated.json", "packages/vitest-adapter/.experiment-private/runtime",
                 ".env.private", "fixtures/pure/node_modules/private.js"]
        for name in names:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("excluded")
        self.git("add", "-f", *names)
        self.git("commit", "-qm", "generated fixtures")
        revision = self.git("rev-parse", "HEAD").strip()
        (self.repo / "target/ignored-local").write_text("ignored")
        files = self.contents(self.build(revision=revision))
        self.assertFalse(set(names) & files.keys())
        self.assertFalse(any(".git" in Path(name).parts for name in files))

    def test_invalid_versions_and_missing_revision(self):
        for version in ("../escape", "/absolute", "01.0.0", "1.0", "1.0.0-01", "1.0.0\n"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "SemVer"):
                self.build(version=version)
        with self.assertRaises(subprocess.CalledProcessError):
            self.build(revision="missing-revision")

    def test_metadata_mismatches(self):
        changes = [
            ("Cargo.toml", 'version = "1.0.0-rc.1"', 'version = "0.0.0"'),
            ("Cargo.lock", 'version = "1.0.0-rc.1"', 'version = "0.0.0"'),
            ("package-lock.json", '"version": "1.0.0-rc.1"', '"version": "0.0.0"'),
            ("packages/vitest-adapter/package.json", '"version": "1.0.0-rc.1"', '"version": "0.0.0"'),
            ("package.json", '"ajv": "8.20.0"', '"ajv": "0.0.0"'),
        ]
        for name, before, after in changes:
            with self.subTest(name=name):
                path = self.repo / name
                original = path.read_text()
                self.assertIn(before, original)
                path.write_text(original.replace(before, after, 1))
                self.commit()
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    self.build(revision=self.git("rev-parse", "HEAD").strip())
                path.write_text(original)
                self.commit()

    def test_missing_required_payload(self):
        (self.repo / "scripts/install-source.py").unlink()
        self.commit()
        with self.assertRaisesRegex(ValueError, "missing required"):
            self.build(revision=self.git("rev-parse", "HEAD").strip())

    def test_unsafe_paths_and_links(self):
        for name in ("../escape", "/absolute", "a/../b", "a//b", "a\\b", "a\nb", "C:/drive"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unsafe"):
                BUILDER.safe_path(name)
        (self.repo / "linked-runtime").symlink_to("/etc/passwd")
        self.commit()
        with self.assertRaisesRegex(ValueError, "regular files"):
            self.build(revision=self.git("rev-parse", "HEAD").strip())


if __name__ == "__main__":
    unittest.main()
