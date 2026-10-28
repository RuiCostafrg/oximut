"""Lightweight CLI contract checks; build `cargo build -p oximut-cli --locked` first."""

import json
import pathlib
import subprocess
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
BINARY = REPO / "target/debug/oximut"
FIXTURE = REPO / "fixtures/m1"
SOURCE = FIXTURE / "src/subject.ts"


class CliContract(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            [str(BINARY), *map(str, args)], cwd=REPO, text=True,
            capture_output=True, timeout=30,
        )

    def fixture_args(self):
        return ["--root", FIXTURE, "--config", FIXTURE / "vitest.config.ts",
                "--source", SOURCE]

    def test_help_and_bad_invocation_exit_codes(self):
        help_result = self.invoke("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--list", help_result.stdout)

        invalid = self.invoke("--not-a-real-option")
        self.assertEqual(invalid.returncode, 2, invalid.stdout + invalid.stderr)
        self.assertTrue(invalid.stderr.strip())

    def test_configuration_error_has_no_report(self):
        with tempfile.TemporaryDirectory(prefix="oximut-contract-") as directory:
            report = pathlib.Path(directory) / "report.json"
            result = self.invoke(
                "--root", FIXTURE, "--config", pathlib.Path(directory) / "missing.ts",
                "--source", SOURCE, "--report", report,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertTrue(result.stderr.strip())
            self.assertFalse(report.exists(), "configuration errors must not leave a misleading report")

    def test_list_v1_envelope_and_location_types(self):
        result = self.invoke(*self.fixture_args(), "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        listing = json.loads(result.stdout)
        self.assertEqual(listing["schemaVersion"], 1)
        self.assertIsInstance(listing["mutants"], list)
        self.assertIsInstance(listing["diagnostics"], list)
        self.assertTrue(listing["mutants"])
        for mutant in listing["mutants"]:
            for field in ("id", "file", "operator", "original", "replacement", "code"):
                self.assertIsInstance(mutant[field], str)
            self.assertIn("map", mutant)
            self.assertIsInstance(mutant["location"], dict)
            for endpoint in ("start", "end"):
                position = mutant["location"][endpoint]
                self.assertIsInstance(position["offset"], int)
                self.assertIsInstance(position["line"], int)
                self.assertIsInstance(position["column"], int)
                self.assertGreaterEqual(position["offset"], 0)
                self.assertGreaterEqual(position["line"], 1)
                self.assertGreaterEqual(position["column"], 1)


if __name__ == "__main__":
    unittest.main()
