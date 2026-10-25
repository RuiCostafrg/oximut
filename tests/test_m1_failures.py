"""CLI failure-path gates using a small fake adapter process."""

import json
import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
BINARY = REPO / "target/debug/oximut"
SOURCE = REPO / "fixtures/m1/src/subject.ts"


FAKE_ADAPTER = r"""
import { appendFile, readFile, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';

const [requestPath, responsePath] = process.argv.slice(-2);
const request = JSON.parse(await readFile(requestPath, 'utf8'));
const mode = process.env.OXIMUT_FAKE_MODE ?? 'pass';
const logPath = process.env.OXIMUT_FAKE_LOG;
if (logPath) await appendFile(logPath, `${request.kind}\n`);
if (mode === 'orphan' || mode === 'hang') {
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
    detached: true, stdio: 'ignore',
  });
  child.unref();
  await writeFile(process.env.OXIMUT_FAKE_PID, String(child.pid));
  if (mode === 'hang') await new Promise(() => { setInterval(() => {}, 1000); });
}

if (mode === 'process-failure' && request.kind === 'mutant') process.exit(23);
if (mode === 'malformed' && request.kind === 'mutant') {
  await writeFile(responsePath, '{ this is not JSON');
  process.exit(0);
}

const identity = {
  protocol: request.protocol,
  runId: request.runId,
  jobId: request.jobId,
  generation: request.generation,
};
if (mode === 'stale' && request.kind === 'mutant') identity.jobId = 'old-job';
const result = request.kind === 'baseline' && mode === 'baseline-failure'
  ? { outcome: 'failed', tests: { passed: 0, failed: 1, skipped: 0 }, errors: [] }
  : { outcome: 'passed', tests: { passed: 1, failed: 0, skipped: 0 }, errors: [] };
await writeFile(responsePath, JSON.stringify({ ...identity, ...result }));
"""


class CliFailurePaths(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="oximut-m1-failures-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = pathlib.Path(self.temporary.name)
        self.adapter = self.directory / "fake-adapter.mjs"
        self.adapter.write_text(textwrap.dedent(FAKE_ADAPTER))
        self.report = self.directory / "report.json"
        self.log = self.directory / "requests.log"

    def invoke(self, mode):
        root = REPO / "fixtures/m1"
        environment = os.environ.copy()
        environment["OXIMUT_FAKE_MODE"] = mode
        environment["OXIMUT_FAKE_LOG"] = str(self.log)
        environment["OXIMUT_FAKE_PID"] = str(self.directory / "child.pid")
        return subprocess.run(
            [
                str(BINARY), "--root", str(root), "--config", str(root / "vitest.config.ts"),
                "--source", str(SOURCE), "--adapter", str(self.adapter),
                "--report", str(self.report),
                "--timeout-ms", "1000",
            ],
            cwd=REPO,
            env=environment,
            text=True,
            capture_output=True,
            timeout=120,
        )

    def report_json(self, result):
        self.assertTrue(self.report.exists(), result.stdout + result.stderr)
        return json.loads(self.report.read_text())

    def test_malformed_adapter_response_is_infrastructure_error_not_kill(self):
        result = self.invoke("malformed")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        report = self.report_json(result)
        self.assertGreater(report["summary"]["infrastructureError"], 0)
        self.assertEqual(report["summary"]["killed"], 0)

    def test_stale_adapter_identity_is_infrastructure_error_not_kill(self):
        result = self.invoke("stale")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        report = self.report_json(result)
        self.assertGreater(report["summary"]["infrastructureError"], 0)
        self.assertEqual(report["summary"]["killed"], 0)

    def test_adapter_process_failure_is_infrastructure_error_not_kill(self):
        result = self.invoke("process-failure")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        report = self.report_json(result)
        self.assertGreater(report["summary"]["infrastructureError"], 0)
        self.assertEqual(report["summary"]["killed"], 0)

    def test_failed_baseline_prevents_mutant_execution(self):
        result = self.invoke("baseline-failure")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        report = self.report_json(result)
        self.assertEqual(report["baseline"]["outcome"], "failed")
        self.assertEqual(len(report["mutants"]), 3)
        self.assertTrue(all(m["outcome"] == "skipped" for m in report["mutants"]))
        self.assertEqual(report["summary"]["killed"], 0)
        self.assertIsNone(report["summary"]["score"])
        self.assertEqual(self.log.read_text().splitlines(), ["baseline"])

    def test_adapter_leaving_detached_child_is_not_success(self):
        self.check_owned_descendant_cleanup("orphan")

    def test_unresponsive_adapter_is_stopped_with_its_detached_child(self):
        self.check_owned_descendant_cleanup("hang")

    def check_owned_descendant_cleanup(self, mode):
        result = self.invoke(mode)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        report = self.report_json(result)
        self.assertEqual(report["baseline"]["outcome"], "infrastructure-error")
        self.assertEqual(report["summary"]["killed"], 0)
        pid = int((self.directory / "child.pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
