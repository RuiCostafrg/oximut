"""One instrumented baseline validates the exact modules every mutant job serves."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / 'fixtures/matched-scale'
SOURCES = [ROOT / 'src/arithmetic-0.ts', ROOT / 'src/arithmetic-1.ts']
ADAPTER = r"""
import { appendFileSync, existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const session = process.argv[2];
const mode = process.env.OXIMUT_FAKE_MODE;
let sequence = 0;
for (;;) {
  if (existsSync(join(session, 'stop'))) process.exit(0);
  const input = join(session, `request-${sequence}.json`);
  if (!existsSync(input)) { await new Promise(r => setTimeout(r, 5)); continue; }
  const request = JSON.parse(readFileSync(input, 'utf8'));
  appendFileSync(process.env.OXIMUT_FAKE_LOG, JSON.stringify(request) + '\n');
  const instrumented = request.jobId === 'instrumented-baseline';
  const failed = instrumented && mode === 'fail';
  const passed = instrumented && mode === 'count' ? 2 : failed ? 0 : 1;
  const response = { protocol: request.protocol, runId: request.runId, jobId: request.jobId,
    generation: request.generation, outcome: failed ? 'failed' : 'passed',
    tests: { passed, failed: failed ? 1 : 0, skipped: 0 }, errors: [] };
  const output = join(session, `response-${sequence++}.json`);
  writeFileSync(output + '.tmp', JSON.stringify(response));
  renameSync(output + '.tmp', output);
}
"""


class InstrumentedBaseline(unittest.TestCase):
    def run_cli(self, mode='', selection=False):
        with tempfile.TemporaryDirectory(prefix='oximut-instrumented-') as directory:
            adapter = Path(directory) / 'adapter.mjs'
            adapter.write_text(ADAPTER)
            log = Path(directory) / 'requests.jsonl'
            command = [
                str(REPO / 'target/debug/oximut'), '--root', str(ROOT),
                '--execution', 'persistent', '--adapter', str(adapter), '--report', '-',
                '--timeout-ms', '5000', '--no-cache',
            ]
            for source in SOURCES:
                command += ['--source', str(source)]
            if selection:
                command.append('--test-selection')
            result = subprocess.run(
                command, env={**os.environ, 'OXIMUT_FAKE_MODE': mode, 'OXIMUT_FAKE_LOG': str(log)},
                text=True, capture_output=True, timeout=120)
            self.assertIn(result.returncode, (0, 1, 2), result.stderr)
            requests = [json.loads(line) for line in log.read_text().splitlines()]
            return json.loads(result.stdout), requests

    def assert_single_validation(self, report, requests, selection):
        self.assertEqual([r['jobId'] for r in requests[:2]], ['baseline', 'instrumented-baseline'])
        original, instrumented = requests[:2]
        self.assertNotIn('resident', original)
        self.assertIsNone(original['mutation'])
        self.assertEqual(instrumented['kind'], 'baseline')
        self.assertIsNone(instrumented['mutation'])
        resident = {item['file']: item for item in instrumented['resident']}
        self.assertEqual(sorted(resident), sorted(map(str, SOURCES)))
        for item in resident.values():
            self.assertIn('process.env.OXIMUT_ACTIVE_MUTANT', item['code'])
            self.assertEqual('__oximut_m3_hit__' in item['code'], selection)
        if selection:
            coverage = {item['file']: item for item in instrumented['collectCoverage']}
            self.assertEqual(sorted(coverage), sorted(map(str, SOURCES)))
            for file, item in resident.items():
                # Coverage observes exactly the module mutant jobs execute.
                self.assertEqual(coverage[file]['code'], item['code'])
                self.assertEqual(coverage[file]['map'], item['map'])
        else:
            self.assertNotIn('collectCoverage', instrumented)
        mutants = requests[2:]
        self.assertGreater(len(mutants), 2)
        self.assertEqual(sum(r['jobId'] == 'instrumented-baseline' for r in requests), 1)
        self.assertFalse(any(r['jobId'].startswith('__oximut_disabled__') for r in requests))
        files = set()
        for request in mutants:
            self.assertEqual(request['kind'], 'mutant')
            self.assertNotIn('collectCoverage', request)
            self.assertEqual(request['resident'], instrumented['resident'])
            mutation = request['mutation']
            files.add(mutation['file'])
            self.assertEqual(mutation['code'], resident[mutation['file']]['code'])
        self.assertEqual(files, set(map(str, SOURCES)))
        resources = report['resources']
        self.assertIn('instrumentedBaseline', resources['phaseWallMs'])
        self.assertNotIn('disabledSwitchValidation', resources['phaseWallMs'])
        self.assertNotIn('coverageBaseline', resources['phaseWallMs'])
        [run] = resources['disabledValidationRuns']
        self.assertEqual(sorted(run['files']), sorted(map(str, SOURCES)))
        self.assertEqual((run['run'], run['coverage']), ('instrumentedBaseline', selection))
        self.assertGreaterEqual(run['wallMs'], 0)

    def test_one_validation_run_serves_every_switched_module_to_every_mutant(self):
        report, requests = self.run_cli()
        self.assertEqual(report['baseline']['outcome'], 'passed')
        self.assert_single_validation(report, requests, selection=False)

    def test_selection_collects_coverage_from_the_validated_modules(self):
        report, requests = self.run_cli(selection=True)
        self.assertEqual(report['baseline']['outcome'], 'passed')
        self.assert_single_validation(report, requests, selection=True)

    def test_failed_or_count_changing_validation_blocks_every_mutant(self):
        for mode, outcome, message in [
            ('fail', 'failed', 'instrumented baseline failed'),
            ('count', 'infrastructure-error', 'changed baseline test counts'),
        ]:
            with self.subTest(mode=mode):
                report, requests = self.run_cli(mode)
                self.assertEqual([r['jobId'] for r in requests],
                                 ['baseline', 'instrumented-baseline'])
                self.assertEqual(report['baseline']['outcome'], outcome)
                self.assertFalse(report['summary']['complete'])
                evidence = json.dumps(report)
                self.assertIn(message, evidence)
                self.assertTrue(all(m['outcome'] == 'skipped' for m in report['mutants']))


if __name__ == '__main__':
    unittest.main()
