"""R3 security/integrity and mocked lifecycle checks; no build/install/network."""
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest import mock

import test_distribution as distribution_fixture

SPEC = importlib.util.spec_from_file_location("artifact_validator", Path(__file__).resolve().parents[1] / "scripts/validate-source.py")
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
VERSION = "1.0.0-rc.1"


class ArtifactValidation(unittest.TestCase):
    setUp = distribution_fixture.SourceDistribution.setUp
    git = distribution_fixture.SourceDistribution.git
    commit = distribution_fixture.SourceDistribution.commit
    build = distribution_fixture.SourceDistribution.build

    def candidate(self):
        archive = self.build()
        self.tree = self.git("rev-parse", "HEAD^{tree}").strip()
        return archive

    def inspect(self, archive):
        return GATE.inspect_archive(archive, VERSION, self.revision, self.tree)

    def rewrite(self, archive, change, *, pax_headers=None):
        with tarfile.open(archive) as tar:
            entries = [(entry, tar.extractfile(entry).read()) for entry in tar]
        change(entries)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT,
                          pax_headers=pax_headers) as tar:
            for entry, data in entries:
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
        archive.write_bytes(gzip.compress(buffer.getvalue()))
        self.sidecar(archive)

    def sidecar(self, archive):
        archive.with_name(archive.name + ".sha256").write_text(
            f"{GATE.BUILDER.sha(archive.read_bytes())}  {archive.name}\n")

    def test_exact_candidate_and_manual_extraction(self):
        archive = self.candidate()
        files, manifest, digest = self.inspect(archive)
        GATE.extract(files, self.base / "private")
        self.assertEqual((self.base / "private/Cargo.lock").read_bytes(), files["Cargo.lock"][1])
        self.assertEqual(manifest["source"]["commit"], self.revision)
        self.assertEqual(digest, GATE.BUILDER.sha(archive.read_bytes()))
        with self.assertRaises(FileExistsError):
            GATE.extract(files, self.base / "private")

    def test_sidecar_and_revision_are_external_constraints(self):
        archive = self.candidate()
        with self.assertRaisesRegex(ValueError, "revision/tree"):
            GATE.inspect_archive(archive, VERSION, "0" * 40, self.tree)
        with self.assertRaisesRegex(ValueError, "revision/tree"):
            GATE.inspect_archive(archive, VERSION, self.revision, "0" * 40)
        archive.write_bytes(archive.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "sidecar"):
            self.inspect(archive)

    def test_paths_types_duplicates_and_modes(self):
        archive = self.candidate()
        original = archive.read_bytes()
        names = ("../escape", "/absolute", f"oximut-{VERSION}/../escape",
                 f"oximut-{VERSION}/a//b", f"oximut-{VERSION}/a\\b",
                 "other-root/file", f"oximut-{VERSION}/C:/drive")
        def set_name(name):
            return lambda entries: setattr(entries[0][0], "name", name)
        changes = [(set_name(name), "unsafe|root") for name in names]
        changes += [(lambda entries: entries.append(entries[0]), "duplicate")]
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            def change(entries, kind=kind):
                entries[0][0].type = kind
                entries[0][0].linkname = "/etc/passwd"
            changes.append((change, "regular files"))
        changes.append((lambda entries: setattr(entries[0][0], "mode", 0o4755), "mode"))
        for change, error in changes:
            with self.subTest(error=error):
                archive.write_bytes(original)
                self.rewrite(archive, change)
                with self.assertRaisesRegex(ValueError, error):
                    self.inspect(archive)

    def test_pax_ownership_and_timestamps_are_rejected(self):
        archive = self.candidate()
        original = archive.read_bytes()
        self.rewrite(archive, lambda entries: None, pax_headers={"path": ""})
        with self.assertRaisesRegex(ValueError, "extended archive headers forbidden"):
            self.inspect(archive)
        for field, value, error in (
            ("uid", 1, "unexpected archive ownership"),
            ("uname", "owner", "unexpected archive ownership"),
            ("mtime", 1, "commit timestamp mismatch"),
        ):
            with self.subTest(field=field):
                archive.write_bytes(original)
                self.rewrite(archive, lambda entries: setattr(entries[0][0], field, value))
                with self.assertRaisesRegex(ValueError, error):
                    self.inspect(archive)

    def test_builder_digest_is_bound_to_archived_bytes(self):
        archive = self.candidate()
        original = archive.read_bytes()
        for alter_builder in (False, True):
            def change(entries):
                by_name = {entry.name.split("/", 1)[1]: (entry, data) for entry, data in entries}
                manifest = json.loads(by_name[GATE.MANIFEST][1])
                if alter_builder:
                    entry, data = by_name["scripts/build-source.py"]
                    by_name["scripts/build-source.py"] = (entry, data + b"\n# changed builder\n")
                    # Keep inventory and all checksums consistent so the stale
                    # builder digest is the only integrity discrepancy.
                    for item in manifest["inventory"]:
                        if item["path"] == "scripts/build-source.py":
                            data = by_name[item["path"]][1]
                            item.update(size=len(data), sha256=GATE.BUILDER.sha(data))
                else:
                    manifest["builderSha256"] = "0" * 64
                by_name[GATE.MANIFEST] = (by_name[GATE.MANIFEST][0], GATE.BUILDER.json_bytes(manifest))
                sums = "".join(f"{GATE.BUILDER.sha(data)}  {name}\n"
                               for name, (_, data) in sorted(by_name.items()) if name != GATE.SUMS)
                by_name[GATE.SUMS] = (by_name[GATE.SUMS][0], sums.encode())
                entries[:] = list(by_name.values())
            with self.subTest(alter_builder=alter_builder):
                archive.write_bytes(original)
                self.rewrite(archive, change)
                with self.assertRaisesRegex(ValueError, "builder digest mismatch"):
                    self.inspect(archive)

    def test_bounded_compressed_expanded_members_and_count(self):
        archive = self.candidate()
        for limit in ("MAX_COMPRESSED", "MAX_EXPANDED", "MAX_FILE", "MAX_ENTRIES"):
            with self.subTest(limit=limit), mock.patch.object(GATE, limit, 1):
                with self.assertRaisesRegex(ValueError, "limit exceeded"):
                    self.inspect(archive)

    def test_inventory_sums_metadata_and_truncation(self):
        archive = self.candidate()
        original = archive.read_bytes()
        def edit(name, transform):
            def change(entries):
                for i, (entry, data) in enumerate(entries):
                    if entry.name.endswith("/" + name):
                        entries[i] = (entry, transform(data))
                        return
                self.fail("fixture entry missing")
            return change
        for change, error in (
            (edit("README.md", lambda data: data + b"tamper"), "inventory"),
            (edit(GATE.SUMS, lambda data: data + b"extra"), "checksums"),
            (edit(GATE.MANIFEST, lambda data: data.replace(b'"formatVersion": 1', b'"formatVersion": 2')), "identity"),
            (edit(GATE.MANIFEST, lambda data: data.replace(b'"inventory": [', b'"inventory": [], "inventory": [')), "duplicate JSON"),
            (lambda entries: entries.pop(), "inventory|metadata"),
        ):
            archive.write_bytes(original)
            self.rewrite(archive, change)
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                self.inspect(archive)
        archive.write_bytes(original[:-5])
        self.sidecar(archive)
        with self.assertRaises((EOFError, ValueError)):
            self.inspect(archive)
        archive.write_bytes(gzip.compress(gzip.decompress(original) + b"hidden second archive"))
        self.sidecar(archive)
        with self.assertRaisesRegex(ValueError, "trailing"):
            self.inspect(archive)

    def test_install_once_remove_source_and_relocate_exact_binary(self):
        archive = self.candidate()
        output = self.base / "retained output"
        installations, smokes = [], []
        def run(command, cwd, **kwargs):
            command = list(map(str, command))
            self.assertNotEqual(Path(cwd), GATE.Path(__file__).resolve().parents[1])
            if command[-1] == "--version":
                return "mock tool version"
            if command[1].endswith("install-source.py"):
                installations.append(command)
                prefix = Path(command[-1])
                (prefix / "lib/oximut/node_modules").mkdir(parents=True)
                (prefix / "lib/oximut/oximut").write_bytes(b"exact compiled candidate")
                return "mock install"
            self.assertTrue(command[1].endswith("smoke-install.py"))
            self.assertFalse(any(Path(cwd).parent.glob("oximut-1*")), "source must be removed")
            self.assertTrue((Path(cwd) / "docs/report.schema.json").is_file())
            prefix = Path(command[command.index("--prefix") + 1])
            self.assertEqual((prefix / "lib/oximut/oximut").read_bytes(), b"exact compiled candidate")
            smokes.append(prefix)
            return "mock smoke"
        with mock.patch.object(GATE, "run", side_effect=run):
            evidence = GATE.validate(archive, VERSION, self.revision, self.tree, output)
        self.assertEqual(len(installations), 1)
        self.assertEqual(len(smokes), 2)
        self.assertFalse(smokes[0].exists())
        self.assertTrue(smokes[1].exists())
        self.assertIn(" ", str(smokes[1]))
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(json.loads((output / "evidence.json").read_text()), evidence)

    def test_failure_evidence_and_no_install_for_invalid_artifact(self):
        archive = self.candidate()
        output = self.base / "failed"
        with mock.patch.object(GATE, "run", side_effect=RuntimeError("injected failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                GATE.validate(archive, VERSION, self.revision, self.tree, output)
        self.assertEqual(json.loads((output / "evidence.json").read_text())["status"], "failed")
        archive.write_bytes(b"bad")
        with mock.patch.object(GATE, "run") as run:
            with self.assertRaises(ValueError):
                GATE.validate(archive, VERSION, self.revision, self.tree, self.base / "invalid")
        run.assert_not_called()
