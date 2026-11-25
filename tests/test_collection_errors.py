"""A mutant that makes a test file throw while Vitest collects it is killed, in every mode.

A `describe` body that calls the mutated code runs at collection time. When the mutant makes
that call throw, the file fails with an error and no test fails. That is the code under test
failing (StrykerJS reports the mutant as detected too), not a fault of the tool, so it must
not be an infrastructure error. The same error in the unmutated baseline is a failed baseline.

Build first: `cargo build -p oximut-cli --locked`.
"""
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from test_m1 import BINARY, REPO

ADDON = REPO / 'packages/vitest-adapter/native/build/zygote-fork.node'
MODES = {
    'reference': ('--execution', 'fresh', '--no-zygote', '--no-test-selection'),
    'persistent': ('--execution', 'persistent', '--no-zygote', '--no-test-selection'),
    'shared-lanes': ('--execution', 'persistent', '--no-zygote', '--no-test-selection', '--workers', '2'),
    'per-worker-controllers': ('--execution', 'persistent', '--no-zygote', '--no-test-selection',
                               '--no-shared-controller', '--workers', '2'),
    'defaults': (),
}
if ADDON.is_file() or os.environ.get('OXIMUT_REQUIRE_ZYGOTE_ADDON') == '1':
    MODES['zygote'] = ('--execution', 'persistent', '--zygote', '--no-test-selection',
                       '--no-zygote-precollect', '--no-rolling-lanes')

SOURCE = """export function pick(list: string[], index: number): string {
  return list[index + 1].toUpperCase();
}
"""
# `first` is computed in the describe body, during collection, never inside a test.
COLLECTING = """import { describe, expect, it } from 'vitest';
import { pick } from './source';
describe('table', () => {
  const first = pick(['a', 'b'], 0);
  it('picks the next entry', () => expect(first).toBe('B'));
});
"""
UNRELATED = """import { expect, it } from 'vitest';
it('unrelated', () => expect(1 + 1).toBe(2));
"""
BROKEN = """import { describe, it } from 'vitest';
describe('table', () => {
  throw new Error('collection exploded');
  it('never registered', () => {});
});
"""


class CollectionErrors(unittest.TestCase):
    def invoke(self, tests, mode):
        with tempfile.TemporaryDirectory(prefix='oximut-collection-error-') as temporary:
            root = pathlib.Path(temporary)
            (root / 'node_modules').symlink_to(REPO / 'node_modules')
            (root / 'source.ts').write_text(SOURCE)
            (root / 'vitest.config.mjs').write_text(
                "export default { test: { include: ['*.test.ts'], testTimeout: 10000 } };")
            for name, content in tests.items():
                (root / name).write_text(content)
            report = root / 'report.json'
            result = subprocess.run([
                str(BINARY), '--root', str(root), '--source', 'source.ts',
                '--config', 'vitest.config.mjs', '--no-cache', '--mutators', 'arithmetic',
                '--report', str(report), *MODES[mode],
            ], cwd=REPO, capture_output=True, text=True, timeout=180)
            self.assertTrue(report.is_file(), result.stdout + result.stderr)
            return result, json.loads(report.read_text())

    def test_mutant_that_breaks_collection_is_killed_in_every_mode(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                result, report = self.invoke(
                    {'collecting.test.ts': COLLECTING, 'unrelated.test.ts': UNRELATED}, mode)
                detail = result.stdout + result.stderr + json.dumps(report)
                self.assertEqual(report['baseline']['outcome'], 'passed', detail)
                self.assertEqual([m['outcome'] for m in report['mutants']], ['killed'], detail)
                mutant = report['mutants'][0]
                self.assertTrue(any('toUpperCase' in error for error in mutant['errors']), detail)
                self.assertEqual(report['summary']['infrastructureError'], 0, detail)
                self.assertEqual(report['summary']['score'], 100.0, detail)
                self.assertTrue(report['summary']['complete'], detail)
                self.assertEqual(result.returncode, 0, detail)

    def test_collection_error_in_the_baseline_blocks_mutation_testing(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                result, report = self.invoke({'broken.test.ts': BROKEN}, mode)
                detail = result.stdout + result.stderr + json.dumps(report)
                self.assertEqual(result.returncode, 2, detail)
                self.assertEqual(report['baseline']['outcome'], 'failed', detail)
                self.assertTrue(any('collection exploded' in e for e in report['baseline']['errors']), detail)
                self.assertTrue(all(m['outcome'] == 'skipped' for m in report['mutants']), detail)
                self.assertEqual(report['summary']['killed'], 0, detail)
                self.assertIsNone(report['summary']['score'], detail)
                self.assertFalse(report['summary']['complete'], detail)


if __name__ == '__main__':
    unittest.main()
