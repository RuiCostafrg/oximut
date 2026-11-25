"""A mutant whose tests take longer than a short fixed deadline still gets a verdict.

The `deadlines` fixture's one test sleeps 2.5 s. With an explicit 2 s deadline every
mutant that runs it times out; with the derived deadline (the baseline time of the
selected file times 1.5, plus a constant) the same mutants are killed. This is what
a fixed 30 s deadline got wrong for suites of a minute and mutants that load the
whole suite. Build `cargo build -p oximut-cli --locked` first.
"""
import json
import math
import pathlib
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
BINARY = REPO / 'target/debug/oximut'
ROOT = REPO / 'fixtures/deadlines'


class ScaledDeadlines(unittest.TestCase):
    def run_cli(self, *flags):
        with tempfile.TemporaryDirectory(prefix='oximut-deadlines-') as directory:
            report = pathlib.Path(directory) / 'report.json'
            result = subprocess.run(
                [str(BINARY), '--root', str(ROOT), '--config', str(ROOT / 'vitest.config.ts'),
                 '--source', 'src/subject.ts', '--no-cache', '--report', str(report), *flags],
                cwd=REPO, text=True, capture_output=True, timeout=240)
            self.assertTrue(report.exists(), result.stdout + result.stderr)
            return json.loads(report.read_text()), result

    def test_an_explicit_short_deadline_times_the_slow_mutants_out(self):
        report, result = self.run_cli('--timeout-ms', '2000', '--baseline-timeout-ms', '120000')
        self.assertEqual(report['baseline']['outcome'], 'passed', result.stdout + result.stderr)
        self.assertGreaterEqual(len(report['mutants']), 2)
        self.assertEqual({m['outcome'] for m in report['mutants']}, {'timeout'})
        self.assertEqual({m['deadlineMs'] for m in report['mutants']}, {2000})
        self.assertEqual(result.returncode, 2)

    def test_the_derived_deadline_follows_the_time_the_mutants_tests_take(self):
        # A small constant keeps the check quick; 1.5 times the file's 2.5 s is the rest.
        report, result = self.run_cli('--timeout-constant-ms', '8000')
        self.assertEqual(report['baseline']['outcome'], 'passed', result.stdout + result.stderr)
        self.assertGreaterEqual(len(report['mutants']), 2)
        self.assertEqual({m['outcome'] for m in report['mutants']}, {'killed'}, report['mutants'])
        timings = {pathlib.Path(t['file']).name: t['durationMs']
                   for t in report['baseline']['fileTimings']}
        self.assertGreaterEqual(timings['slow.test.ts'], 2500)
        for mutant in report['mutants']:
            self.assertEqual(mutant['selection']['files'], [str(ROOT / 'test/slow.test.ts')])
            # Time measured in the baseline, scaled, plus the constant; far above 2 s.
            self.assertEqual(mutant['deadlineMs'], math.ceil(timings['slow.test.ts'] * 1.5) + 8000)
            self.assertGreater(mutant['deadlineMs'], 8000 + 1.5 * 2500 - 1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report['summary']['complete'])


if __name__ == '__main__':
    unittest.main()
