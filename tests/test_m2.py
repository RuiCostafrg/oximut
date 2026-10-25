"""Compare mutation switching/reuse with the independent fresh-process reference."""
import json
import pathlib
import subprocess
import tempfile
import unittest

from test_m1 import BINARY, REPO, sources


class PersistentEquivalence(unittest.TestCase):
    def run_fixture(self, fixture, source, mode, seed):
        root = REPO / 'fixtures' / fixture
        with tempfile.TemporaryDirectory(prefix='oximut-m2-acceptance-') as temporary:
            report = pathlib.Path(temporary) / 'report.json'
            result = subprocess.run([
                str(BINARY), '--root', str(root), '--config', str(root / 'vitest.config.ts'),
                '--source', str(root / 'src' / source), '--execution', mode,
                '--order-seed', str(seed), '--report', str(report),
            ], cwd=REPO, text=True, capture_output=True, timeout=180)
            self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
            payload = json.loads(report.read_text())
            self.assertEqual(payload['baseline']['outcome'], 'passed')
            self.assertTrue(payload['summary']['complete'], payload)
            return payload

    def test_randomized_orders_match_fresh_reference_without_source_edits(self):
        before = sources()
        for fixture, source in [('m1', 'subject.ts'), ('stateful', 'service.ts'), ('m2', 'subject.ts')]:
            with self.subTest(fixture=fixture):
                reference = self.run_fixture(fixture, source, 'fresh', 0)
                expected = {m['id']: m['outcome'] for m in reference['mutants']}
                for seed in (17, 83):
                    reused = self.run_fixture(fixture, source, 'persistent', seed)
                    self.assertEqual({m['id']: m['outcome'] for m in reused['mutants']}, expected)
                    self.assertEqual(reused['baseline']['tests'], reference['baseline']['tests'])
        self.assertEqual(before, sources(), 'Execution changed fixture sources')


if __name__ == '__main__':
    unittest.main()
