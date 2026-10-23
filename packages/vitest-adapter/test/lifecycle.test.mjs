import test from 'node:test';
import assert from 'node:assert/strict';
import { runExperiment } from '../experiment.mjs';

test('Vitest lifecycle agrees with fresh execution, selects exact IDs, reports coverage and kills a CPU-hung process tree', { timeout: 180000 }, async () => {
  const report = await runExperiment();
  assert.equal(report.passed, true, JSON.stringify(report.gates));
  for (const fixture of report.fixtures) {
    assert.equal(fixture.fresh[0].run.outcome, 'passed');
    assert.notEqual(fixture.fresh[0].run.controllerPid, fixture.reused.controllerPid);
    assert.equal(new Set(fixture.reused.runs.map((run) => run.controllerPid)).size, 1);
    assert.ok(fixture.reused.runs[0].tests.length > 1);
    assert.equal(fixture.reused.selection.tests.filter((test) => test.state === 'passed').length, 1);
    assert.ok(fixture.reused.runs[0].coverage.files.some((file) => file.statements.covered > 0));
  }
  assert.equal(report.reuse.observations[0].count, 0);
  assert.equal(report.reuse.observations[1].count, 0);
  assert.equal(report.reuse.observations[0].phase, 'alpha');
  assert.equal(report.reuse.freshBetaPhase, 'beta');
  assert.equal(report.reuse.environmentUpdated, true);
  assert.equal(report.reuse.selection.outcome, 'passed');
  assert.notEqual(report.reuse.observations[0].evaluation, report.reuse.observations[1].evaluation);
  assert.equal(report.cancellation.outcome, 'cancelled');
  assert.ok(report.cancellation.termination.tracked.includes(report.cancellation.started.descendantPid));
  assert.equal(report.cancellation.termination.allExited, true);
  assert.ok(report.cancellation.termination.elapsedMs < 3500);
  assert.equal(report.cancellation.termination.allReaped, true);
  assert.equal(report.deadline.outcome, 'timeout');
  assert.equal(report.deadline.termination.allReaped, true);
  assert.equal(report.normalCleanup.cleanup.allReaped, true);
});
