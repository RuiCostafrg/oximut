"""Source provenance boundaries; no dependency installation or compilation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

import test_distribution as distribution

SPEC = importlib.util.spec_from_file_location(
    "source_metadata", Path(__file__).resolve().parents[1] / "scripts/source-release-metadata.py")
METADATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METADATA)


class SourceMetadata(unittest.TestCase):
    def setUp(self):
        self.fixture = distribution.SourceDistribution()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.archive = self.fixture.build()

    def generate(self, revision=None, origin=None):
        return METADATA.generate(self.fixture.repo, revision or self.fixture.revision,
                                 origin or self.fixture.revision, self.archive)

    def test_complete_lock_inventory_and_pending_gate(self):
        evidence = self.generate()
        sbom = json.loads(Path(str(self.archive) + ".spdx.json").read_text())
        npm = json.loads((self.fixture.repo / "package-lock.json").read_text())
        cargo = METADATA.tomllib.loads((self.fixture.repo / "Cargo.lock").read_text())
        self.assertEqual(len(sbom["packages"]), len(npm["packages"]) - 1 + len(cargo["package"]))
        self.assertEqual(evidence["archive"]["sha256"], METADATA.builder.sha(self.archive.read_bytes()))
        self.assertEqual(evidence["validation"]["exactArtifactBuildInstallRelocation"], "NOT_RUN")
        self.assertTrue(evidence["validation"]["releasePublishing"].startswith("BLOCKED"))
        for package in sbom["packages"]:
            self.assertEqual(package["licenseConcluded"], "NOASSERTION")
            comment = json.loads(package["comment"])
            if comment["lockfile"] == "package-lock.json":
                original = npm["packages"][comment["path"]]
                self.assertEqual(comment["declaredLicense"], original.get("license"))
                self.assertEqual(comment["integrity"], original.get("integrity"))
            else:
                self.assertEqual(package["licenseDeclared"], "NOASSERTION")
        with self.assertRaises(FileExistsError):
            self.generate()

    def test_tampered_archive_cannot_get_evidence(self):
        self.archive.write_bytes(self.archive.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "differs"):
            self.generate()
        self.assertFalse(Path(str(self.archive) + ".evidence.json").exists())

    def test_origin_must_be_current_and_candidate_must_be_ancestor(self):
        (self.fixture.repo / "README.md").write_text("later trusted commit")
        self.fixture.commit()
        later = self.fixture.git("rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(ValueError, "HEAD"):
            self.generate()
        # Earlier candidate remains valid under a later trusted workflow commit.
        self.assertEqual(self.generate(origin=later)["candidateCommit"], self.fixture.revision)
        self.fixture.git("checkout", "--detach", self.fixture.revision)
        with self.assertRaises(subprocess.CalledProcessError):
            self.generate(revision=later)

    def test_full_sha_and_unknown_licenses(self):
        with self.assertRaisesRegex(ValueError, "full lowercase"):
            self.generate(revision="HEAD")
        npm = {"packages": {"node_modules/example": {"version": "1.2.3", "license": "unverified license text"}}}
        cargo = {"package": [{"name": "example", "version": "1.0.0", "source": "git+https://example.invalid/repo#abc"}]}
        result = METADATA.inventory(npm, cargo, "a" * 40, "b" * 64, 0)
        self.assertEqual(result["packages"][0]["licenseDeclared"], "NOASSERTION")
        self.assertIn("unverified license text", result["packages"][0]["comment"])
        self.assertNotIn("externalRefs", result["packages"][1])


if __name__ == "__main__":
    unittest.main()
