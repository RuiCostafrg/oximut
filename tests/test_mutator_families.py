"""CLI family filtering stays deterministic and rejects malformed requests."""

import json
import pathlib
import subprocess
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
BINARY = REPO / "target/debug/oximut"
SOURCE = REPO / "fixtures/m1/src/subject.ts"


class MutatorFamilies(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            [str(BINARY), "--root", str(REPO / "fixtures/m1"), "--source", str(SOURCE), *args],
            cwd=REPO, text=True, capture_output=True, timeout=30,
        )

    def test_repeatable_union_projects_list_and_reports_exclusions(self):
        boolean_only = self.invoke("--list", "--mutators", "boolean-literal")
        union = self.invoke("--list", "--mutators", "boolean-literal", "--mutators", "comparison-boundary")
        self.assertEqual(boolean_only.returncode, 0, boolean_only.stderr)
        self.assertEqual(union.returncode, 0, union.stderr)
        one, combined = json.loads(boolean_only.stdout), json.loads(union.stdout)
        self.assertTrue(all(m["operator"] == "boolean-literal" for m in one["mutants"]))
        self.assertEqual({m["operator"] for m in combined["mutants"]}, {"boolean-literal", "comparison-boundary"})
        self.assertIn("exclusions are not part of this run", " ".join(combined["diagnostics"]))

    def test_unknown_and_empty_families_fail_before_generation(self):
        for family in ("unknown", "", "arithmetic,"):
            with self.subTest(family=family):
                result = self.invoke("--mutators", family, "--list")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("mutator family", result.stderr)


if __name__ == "__main__":
    unittest.main()
