"""Lightweight CLI contract checks; build `cargo build -p oximut-cli --locked` first."""

import json
import os
import pathlib
import subprocess
import tempfile
import tomllib
import unittest

from test_m3_failures import ADAPTER as COVERAGE_ADAPTER


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

    def test_version_matches_cargo_package_without_project_or_execution(self):
        version = tomllib.loads((REPO / "Cargo.toml").read_text())["workspace"]["package"]["version"]
        for flag in ("--version", "-V"):
            result = self.invoke("--root", "/missing-oximut-project", flag)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"oximut {version}\n")
            self.assertEqual(result.stderr, "")

    def test_invalid_options_have_diagnostics_and_no_report(self):
        cases = [
            (["--execution", "unknown"], "--execution"),
            (["--cache-policy", "unknown"], "--cache-policy"),
            (["--cache-env", "BAD=VALUE"], "--cache-env"),
            (["--timeout-ms", "0"], "--timeout-ms"),
            (["--timeout-ms", "86400001"], "--timeout-ms"),
            (["--list", "--baseline-only"], "cannot be combined"),
            (["--order-seed", "-1"], "--order-seed"),
            (["--workers"], "missing value"),
        ]
        with tempfile.TemporaryDirectory(prefix="oximut-options-") as directory:
            report = pathlib.Path(directory) / "report.json"
            for args, diagnostic in cases:
                with self.subTest(args=args):
                    result = self.invoke(*self.fixture_args(), "--report", report, *args)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn(diagnostic, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertFalse(report.exists())

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

    def test_unsupported_resolved_projects_fail_before_tests_in_both_modes(self):
        # Inline projects override the runner's root settings. A marker catches
        # accidental execution under unsupported isolation/resource settings.
        cases = [
            ({"pool": "threads"}, "one Node project"),
            ({"isolate": False}, "one Node project"),
            ({"maxWorkers": 2}, "one Node project"),
            ({"maxConcurrency": 2}, "one Node project"),
            ({"environment": "custom-unsupported-environment"}, "one Node project"),
            ({"browser": {"enabled": True, "instances": [{"browser": "chromium"}]}}, "browser"),
        ]
        (REPO / ".oximut").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="contract-project-", dir=REPO / ".oximut") as directory:
            root = pathlib.Path(directory)
            marker = root / "executed"
            (root / "subject.ts").write_text("export const enabled = true;")
            (root / "sample.test.ts").write_text(
                "import { test } from 'vitest'; import { writeFileSync } from 'node:fs';"
                f"writeFileSync({json.dumps(str(marker))}, 'executed'); test('sample', () => {{}});"
            )
            for execution in ("fresh", "persistent"):
                for settings, diagnostic in cases + [(None, "one Node project")]:
                    with self.subTest(execution=execution, settings=settings):
                        projects = ([{"test": {"name": "one", **settings}}] if settings is not None
                                    else [{"test": {"name": name}} for name in ("one", "two")])
                        config = {"test": {"include": ["sample.test.ts"], "projects": projects}}
                        (root / "vitest.config.ts").write_text("export default " + json.dumps(config))
                        result = self.invoke("--root", root, "--source", "subject.ts",
                                             "--execution", execution, "--report", "-")
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        report = json.loads(result.stdout)
                        self.assertFalse(report["summary"]["complete"])
                        self.assertEqual(report["summary"]["killed"], 0)
                        self.assertIn(diagnostic.lower(), " ".join(report["baseline"]["errors"]).lower())
                        self.assertFalse(marker.exists(), report)

    def test_only_no_coverage_is_complete_but_exits_one(self):
        listing = self.invoke(*self.fixture_args(), "--list")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        ids = [mutant["id"] for mutant in json.loads(listing.stdout)["mutants"]]
        with tempfile.TemporaryDirectory(prefix="oximut-no-coverage-") as directory:
            adapter = pathlib.Path(directory) / "adapter.mjs"
            adapter.write_text(COVERAGE_ADAPTER)
            result = subprocess.run(
                [str(BINARY), *map(str, self.fixture_args()), "--adapter", str(adapter),
                 "--test-selection", "--report", "-"],
                cwd=REPO, text=True, capture_output=True, timeout=30,
                env={**os.environ, "OXIMUT_BAD_COVERAGE": "valid",
                     "OXIMUT_MUTANT_IDS": json.dumps(ids)},
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["summary"]["complete"])
        self.assertEqual(report["summary"]["survived"], 0)
        self.assertEqual(report["summary"]["noCoverage"], len(ids))
        self.assertGreater(len(ids), 0)
        self.assertTrue(all(mutant["provenance"]["source"] == "baseline-coverage"
                            for mutant in report["mutants"]))

    def test_list_v1_envelope_and_location_types(self):
        result = self.invoke(*self.fixture_args(), "--config", "/missing-list-config.ts",
                             "--adapter", "/missing-list-adapter.mjs", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory(prefix="oximut-list-") as directory:
            path = pathlib.Path(directory) / "list.json"
            path.write_text(result.stdout)
            validation = subprocess.run(
                ["node", str(REPO / "scripts/validate-list.mjs"), str(path)],
                cwd=REPO, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(validation.returncode, 0, validation.stderr)
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
