"""CLI family filtering stays deterministic and rejects malformed requests."""

import json
import pathlib
import subprocess
import tempfile
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
        self.assertEqual({m["operator"] for m in one["mutants"]}, {"boolean-literal"})
        self.assertEqual({m["operator"] for m in combined["mutants"]}, {"boolean-literal", "comparison-boundary"})
        self.assertIn("exclusions are not part of this run", " ".join(combined["diagnostics"]))

    def test_unknown_and_empty_families_fail_before_generation(self):
        for family in ("unknown", "", "arithmetic,"):
            with self.subTest(family=family):
                result = self.invoke("--mutators", family, "--list")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("mutator family", result.stderr)


FAMILIES = [
    "arithmetic", "assignment-operator", "boolean-literal", "comparison-boundary",
    "equality-operator", "logical-operator", "unary-operator", "update-operator",
]
OPERATORS = """export function f(a: number, b: number): number {
  let n = 0;
  n += a;
  n++;
  const ok = (!(a < b) && a === b || b) ?? true;
  return ok ? -n + a * b : ~n;
}
"""


class OperatorFamilies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = tempfile.TemporaryDirectory()
        (pathlib.Path(cls.project.name) / "src").mkdir()
        (pathlib.Path(cls.project.name) / "src/ops.ts").write_text(OPERATORS)

    @classmethod
    def tearDownClass(cls):
        cls.project.cleanup()

    def listing(self, *args):
        result = subprocess.run(
            [str(BINARY), "--root", self.project.name, "--source", "src/ops.ts", "--list", *args],
            cwd=REPO, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_every_family_is_selectable_alone_and_by_default(self):
        everything = self.listing()
        self.assertEqual({m["operator"] for m in everything["mutants"]}, set(FAMILIES))
        self.assertIn("mutator families=all", everything["diagnostics"])
        total = 0
        for family in FAMILIES:
            with self.subTest(family=family):
                alone = self.listing("--mutators", family)
                self.assertTrue(alone["mutants"])
                self.assertEqual({m["operator"] for m in alone["mutants"]}, {family})
                chosen = [m["id"] for m in everything["mutants"] if m["operator"] == family]
                self.assertEqual([m["id"] for m in alone["mutants"]], chosen)
                total += len(alone["mutants"])
        self.assertEqual(total, len(everything["mutants"]))

    def test_new_families_union_with_old_ones_and_keep_their_ids(self):
        old = self.listing("--mutators", "arithmetic,boolean-literal,comparison-boundary")
        mixed = self.listing("--mutators", "logical-operator", "--mutators", "arithmetic,update-operator")
        self.assertEqual({m["operator"] for m in mixed["mutants"]}, {"logical-operator", "arithmetic", "update-operator"})
        old_ids = {m["id"] for m in old["mutants"] if m["operator"] == "arithmetic"}
        self.assertEqual({m["id"] for m in mixed["mutants"] if m["operator"] == "arithmetic"}, old_ids)
        # Selecting only the old families generates only their mutants, not a filtered superset.
        self.assertEqual({m["operator"] for m in old["mutants"]}, {"arithmetic", "boolean-literal", "comparison-boundary"})

    def test_the_error_names_every_family(self):
        result = subprocess.run(
            [str(BINARY), "--root", self.project.name, "--source", "src/ops.ts", "--mutators", "string-literal", "--list"],
            cwd=REPO, text=True, capture_output=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        for family in FAMILIES:
            self.assertIn(family, result.stderr)

    def test_help_documents_every_family(self):
        help_text = subprocess.run([str(BINARY), "--help"], text=True, capture_output=True, timeout=30).stdout
        for family in FAMILIES:
            self.assertIn(family, help_text)


if __name__ == "__main__":
    unittest.main()
