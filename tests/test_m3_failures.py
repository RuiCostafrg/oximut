"""Unreliable attribution must never be promoted to no-coverage evidence."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_m1 import BINARY, REPO

ADAPTER = r"""
import { readFileSync, writeFileSync } from 'node:fs';
const [input, output] = process.argv.slice(-2);
const request = JSON.parse(readFileSync(input, 'utf8'));
const mode = process.env.OXIMUT_BAD_COVERAGE;
const response = {
  protocol: request.protocol, runId: request.runId, jobId: request.jobId,
  generation: request.generation, outcome: 'passed',
  tests: { passed: 1, failed: 0, skipped: 0 }, errors: [],
};
if (request.collectCoverage || mode === 'unsolicited') {
  const ids = JSON.parse(process.env.OXIMUT_MUTANT_IDS);
  const tests = [{ id: 'test-1', file: `${request.root}/test/subject.test.ts`, name: 'test' }];
  const hits = ids.map(mutantId => ({ mutantId, tests: [], outsideTest: false }));
  response.coverage = { complete: true, tests, hits, reasons: [] };
  if (mode === 'incomplete') response.coverage.complete = false;
  if (mode === 'duplicate-reference') hits[0].tests = ['test-1', 'test-1'];
  if (mode === 'missing-coverage') delete response.coverage;
  if (mode === 'missing-hit') hits.pop();
  if (mode === 'duplicate-hit') hits.push(hits[0]);
  if (mode === 'unknown-test') hits[0].tests = ['not-collected'];
  if (mode === 'duplicate-test') tests.push(tests[0]);
  if (mode === 'wrong-count') response.tests.passed = 2;
  if (mode === 'outside-root') tests[0].file = '/outside/project.test.ts';
  if (mode === 'failed-probes') {
    response.outcome = 'failed';
    response.tests = { passed: 0, failed: 1, skipped: 0 };
  }
}
writeFileSync(output, JSON.stringify(response));
"""


class AttributionFailurePaths(unittest.TestCase):
    def test_incomplete_or_invalid_inventory_never_establishes_no_coverage(self):
        root = REPO / 'fixtures/m1'
        base = [str(BINARY), '--root', str(root), '--source', 'src/subject.ts']
        generated = subprocess.run(base + ['--list'], cwd=REPO, text=True,
                                   capture_output=True, check=True)
        ids = [mutant['id'] for mutant in json.loads(generated.stdout)['mutants']]
        with tempfile.TemporaryDirectory(prefix='oximut-m3-failure-') as directory:
            adapter = Path(directory) / 'adapter.mjs'
            adapter.write_text(ADAPTER)
            for mode in ('missing-coverage', 'missing-hit', 'duplicate-hit', 'unknown-test',
                         'duplicate-test', 'wrong-count', 'outside-root', 'failed-probes',
                         'unsolicited', 'incomplete', 'duplicate-reference'):
                with self.subTest(mode=mode):
                    result = subprocess.run(
                        base + ['--test-selection', '--adapter', str(adapter), '--report', '-'],
                        cwd=REPO, env={**os.environ, 'OXIMUT_BAD_COVERAGE': mode,
                                       'OXIMUT_MUTANT_IDS': json.dumps(ids)},
                        text=True, capture_output=True, timeout=30,
                    )
                    self.assertIn(result.returncode, (1, 2), result.stdout + result.stderr)
                    report = json.loads(result.stdout)
                    self.assertEqual(report['summary']['noCoverage'], 0, report)
                    self.assertEqual(report['summary']['killed'], 0, report)
                    self.assertTrue(all(m['outcome'] in ('survived', 'skipped', 'infrastructure-error')
                                        for m in report['mutants']), report)
                    if mode == 'failed-probes':
                        self.assertFalse(report['summary']['complete'], report)
                        self.assertTrue(all(m['outcome'] == 'skipped' for m in report['mutants']), report)


if __name__ == '__main__':
    unittest.main()
