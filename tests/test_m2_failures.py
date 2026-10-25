"""Persistent CLI transport must never turn unreliable evidence into success."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
ADAPTER = r"""
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const session = process.argv[2];
const mode = process.env.OXIMUT_FAKE_MODE;
let sequence = 0;
for (;;) {
  if (existsSync(join(session, 'stop'))) process.exit(mode === 'shutdown' ? 23 : 0);
  const input = join(session, `request-${sequence}.json`);
  if (!existsSync(input)) { await new Promise(r => setTimeout(r, 5)); continue; }
  const request = JSON.parse(readFileSync(input, 'utf8'));
  if (mode === 'crash') process.exit(23);
  const response = { protocol: request.protocol, runId: request.runId,
    jobId: mode === 'stale' ? 'old-job' : request.jobId,
    generation: request.generation, outcome: 'passed',
    tests: { passed: 1, failed: 0, skipped: 0 }, errors: [] };
  const output = join(session, `response-${sequence++}.json`);
  writeFileSync(output + '.tmp', mode === 'malformed' ? '{bad' : JSON.stringify(response));
  renameSync(output + '.tmp', output);
}
"""


class PersistentFailurePaths(unittest.TestCase):
    def run_mode(self, mode):
        with tempfile.TemporaryDirectory(prefix='oximut-m2-failure-') as directory:
            adapter = Path(directory) / 'adapter.mjs'
            adapter.write_text(ADAPTER)
            result = subprocess.run([
                str(REPO / 'target/debug/oximut'), '--root', str(REPO / 'fixtures/m1'),
                '--baseline-only', '--execution', 'persistent', '--adapter', str(adapter),
                '--report', '-', '--timeout-ms', '1000',
            ], env={**os.environ, 'OXIMUT_FAKE_MODE': mode},
                text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            report = json.loads(result.stdout)
            self.assertFalse(report['summary']['complete'])
            return report

    def test_malformed_response_is_infrastructure_failure(self):
        self.assertEqual(self.run_mode('malformed')['baseline']['outcome'], 'infrastructure-error')

    def test_stale_response_is_infrastructure_failure(self):
        self.assertEqual(self.run_mode('stale')['baseline']['outcome'], 'infrastructure-error')

    def test_session_crash_is_infrastructure_failure(self):
        self.assertEqual(self.run_mode('crash')['baseline']['outcome'], 'infrastructure-error')

    def test_failed_shutdown_invalidates_successful_evidence(self):
        report = self.run_mode('shutdown')
        self.assertEqual(report['baseline']['outcome'], 'passed')
        self.assertTrue(any('shutdown failed' in d for d in report['diagnostics']))
