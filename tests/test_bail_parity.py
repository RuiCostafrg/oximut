"""Every execution mode stops a mutant's job at its first failing test.

The fixture's first test fails under the mutant and its second hangs forever under the
mutant. A mode that kept running after the failure would reach the hang and report a
timeout instead of the kill; the outcome-parity corpus caught exactly that on the
fresh-process reference. Build `cargo build -p oximut-cli --locked` first.
"""
import json
import pathlib
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
BINARY = REPO / 'target/debug/oximut'

# The modes of benchmarks/parity/run_parity.py, without its diagnostics bookkeeping.
MODES = {
    'fresh': ('--execution', 'fresh', '--no-zygote', '--no-test-selection'),
    'persistent': ('--execution', 'persistent', '--no-zygote', '--no-test-selection'),
    'zygote': ('--execution', 'persistent', '--zygote', '--no-test-selection',
               '--no-zygote-precollect', '--no-rolling-lanes'),
    'defaults': (),
}


class BailParity(unittest.TestCase):
    def test_a_failing_test_before_a_hanging_one_kills_in_every_mode(self):
        with tempfile.TemporaryDirectory(prefix='oximut-bail-parity-') as directory:
            root = pathlib.Path(directory)
            (root / 'node_modules').symlink_to(REPO / 'node_modules')
            (root / 'source.ts').write_text('export const enabled = true;\n')
            (root / 'vitest.config.mjs').write_text(
                "export default { test: { include: ['sample.test.ts'] } };\n")
            (root / 'sample.test.ts').write_text("""
import { test, expect } from 'vitest';
import { enabled } from './source';
test('fails first', () => expect(enabled).toBe(true));
test('hangs when mutated', () => { if (!enabled) while (true) {} });
""")
            seen = {}
            for name, flags in MODES.items():
                with self.subTest(mode=name):
                    report = root / f'{name}.json'
                    result = subprocess.run([
                        str(BINARY), '--root', str(root), '--config', 'vitest.config.mjs',
                        '--source', 'source.ts', '--mutators', 'boolean-literal',
                        '--workers', '1', '--no-cache', '--timeout-ms', '30000',
                        '--report', str(report), *flags,
                    ], cwd=REPO, text=True, capture_output=True, timeout=240)
                    self.assertTrue(report.exists(), result.stdout + result.stderr)
                    payload = json.loads(report.read_text())
                    self.assertEqual(payload['baseline']['outcome'], 'passed', payload)
                    self.assertEqual(len(payload['mutants']), 1, payload['mutants'])
                    mutant = payload['mutants'][0]
                    self.assertEqual(mutant['outcome'], 'killed', mutant)
                    self.assertEqual(mutant['tests']['failed'], 1, mutant)
                    self.assertEqual(mutant['tests']['passed'], 0, mutant)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    seen[name] = (mutant['outcome'], mutant['tests'])
            self.assertEqual(len(set(map(str, seen.values()))), 1, seen)


if __name__ == '__main__':
    unittest.main()
