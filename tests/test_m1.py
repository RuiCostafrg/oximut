"""End-to-end M1 gates; build `cargo build -p oximut-cli --locked` first."""

import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
BINARY = REPO / "target/debug/oximut"


def sources():
    return {
        str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (REPO / "fixtures").rglob("*")
        if path.is_file() and path.suffix in {".ts", ".js", ".json"}
        and not any(part in {"node_modules", ".vitest"} for part in path.parts)
    }


class VerticalSlice(unittest.TestCase):
    def invoke(self, fixture, *args):
        root = REPO / "fixtures" / fixture
        return subprocess.run(
            [str(BINARY), "--root", str(root), "--config",
             str(root / "vitest.config.ts"), *map(str, args)],
            cwd=REPO, text=True, capture_output=True, timeout=120,
        )

    def test_known_outcomes_and_deterministic_generation(self):
        before = sources()
        source = REPO / "fixtures/m1/src/subject.ts"
        first = self.invoke("m1", "--source", source, "--list")
        second = self.invoke("m1", "--source", source, "--list")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        generated = json.loads(first.stdout)["mutants"]
        self.assertEqual(len(generated), 3)
        self.assertEqual(len({m["id"] for m in generated}), 3)
        with tempfile.TemporaryDirectory(prefix="oximut-m1-test-") as temporary:
            report_path = pathlib.Path(temporary) / "report.json"
            result = self.invoke("m1", "--source", source, "--report", report_path)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            report = json.loads(report_path.read_text())
        self.assertEqual(report["schemaVersion"], 1)
        self.assertEqual(report["baseline"]["outcome"], "passed")
        self.assertEqual(report["summary"]["killed"], 2)
        self.assertEqual(report["summary"]["survived"], 1)
        self.assertEqual(report["summary"]["scoreDenominator"], 3)
        self.assertAlmostEqual(report["summary"]["score"], 200 / 3)
        self.assertTrue(report["summary"]["complete"])
        self.assertEqual({m["id"] for m in generated}, {m["id"] for m in report["mutants"]})
        self.assertEqual(before, sources(), "Mutation execution changed fixture sources")

    def test_disabled_mutations_preserve_stateful_behavior(self):
        before = sources()
        with tempfile.TemporaryDirectory(prefix="oximut-m1-test-") as temporary:
            report_path = pathlib.Path(temporary) / "report.json"
            result = self.invoke(
                "stateful", "--source", REPO / "fixtures/stateful/src/service.ts",
                "--baseline-only", "--report", report_path,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(report_path.read_text())
        self.assertEqual(report["baseline"]["outcome"], "passed")
        self.assertGreater(report["baseline"]["tests"]["passed"], 0)
        self.assertEqual(before, sources(), "Baseline execution changed fixture sources")

    def test_stateful_mutants_run_with_fresh_module_state(self):
        before = sources()
        with tempfile.TemporaryDirectory(prefix="oximut-m1-test-") as temporary:
            report_path = pathlib.Path(temporary) / "report.json"
            result = self.invoke(
                "stateful", "--source", REPO / "fixtures/stateful/src/service.ts",
                "--report", report_path,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(report_path.read_text())
        self.assertEqual(report["baseline"]["outcome"], "passed")
        self.assertEqual(report["summary"]["total"], 4)
        self.assertEqual(report["summary"]["killed"], 4)
        self.assertTrue(report["summary"]["complete"])
        self.assertEqual(before, sources(), "Mutation execution changed stateful sources")


if __name__ == "__main__":
    unittest.main()
