"""Each scheduler slot gives Node a private per-run compile cache."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
ADAPTER = r"""
import { appendFileSync, existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const session = process.argv[2];
const parent = process.ppid;
const deadline = Date.now() + 90_000;
let sequence = 0;
for (;;) {
  if (existsSync(join(session, 'stop'))) process.exit(0);
  // Never outlive a hung or killed CLI slot: exit once reparented or overdue.
  if (process.ppid !== parent || Date.now() > deadline) process.exit(1);
  const input = join(session, `request-${sequence}.json`);
  if (!existsSync(input)) { await new Promise(r => setTimeout(r, 5)); continue; }
  const request = JSON.parse(readFileSync(input, 'utf8'));
  appendFileSync(process.env.OXIMUT_FAKE_LOG,
    JSON.stringify({ cache: process.env.NODE_COMPILE_CACHE ?? null }) + '\n');
  const response = { protocol: request.protocol, runId: request.runId, jobId: request.jobId,
    generation: request.generation, outcome: 'passed',
    tests: { passed: 1, failed: 0, skipped: 0 }, errors: [] };
  const output = join(session, `response-${sequence++}.json`);
  writeFileSync(output + '.tmp', JSON.stringify(response));
  renameSync(output + '.tmp', output);
}
"""


class CompileCache(unittest.TestCase):
    def run_cli(self, **environment):
        with tempfile.TemporaryDirectory(prefix='oximut-compile-cache-') as directory:
            adapter = Path(directory) / 'adapter.mjs'
            adapter.write_text(ADAPTER)
            log = Path(directory) / 'log.jsonl'
            env = {**os.environ, 'OXIMUT_FAKE_LOG': str(log), **environment}
            if 'NODE_COMPILE_CACHE' not in environment:
                env.pop('NODE_COMPILE_CACHE', None)
            result = subprocess.run([
                str(REPO / 'target/debug/oximut'), '--root', str(REPO / 'fixtures/m1'),
                '--baseline-only', '--execution', 'persistent', '--adapter', str(adapter),
                '--report', '-', '--timeout-ms', '5000', '--no-cache',
            ], env=env, text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['baseline']['outcome'], 'passed')
            return {json.loads(line)['cache'] for line in log.read_text().splitlines()}

    def test_slots_provide_a_private_cache_removed_after_the_run(self):
        [cache] = self.run_cli()
        path = Path(cache)
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.name, 'node-compile-cache')
        self.assertFalse(path.parent.exists(), 'slot directory must be removed')

    def test_a_callers_compile_cache_setting_is_respected(self):
        with tempfile.TemporaryDirectory() as own:
            self.assertEqual(self.run_cli(NODE_COMPILE_CACHE=own), {own})


if __name__ == '__main__':
    unittest.main()
