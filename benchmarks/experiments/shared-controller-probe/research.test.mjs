import { test } from 'node:test';
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { run } from './probe.mjs';

test('duplicate identity, overlapping calls, and worker-owned errors', async () => {
  const duplicate = await run(['survived'], 2, { mode: 'duplicate' });
  assert.equal(duplicate.events.filter(e => e.type === 'start').length, 2);
  assert.equal(Object.keys(duplicate.outcomes).length, 1); // overwritten, not two occurrences
  const overlap = await run(['survived'], 2, { mode: 'overlap' });
  const execution = overlap.events.filter(e => ['run', 'run-end'].includes(e.type)).map(e => e.type);
  assert.deepEqual(execution, ['run', 'run-end', 'run', 'run-end']);
  const simultaneous = await run(['survived', 'survived-again'], 2, { mode: 'simultaneous' });
  assert.equal(simultaneous.events.slice(0, simultaneous.events.findIndex(e => e.type === 'run-end')).filter(e => e.type === 'run').length, 2);
  const failures = await run(['crash', 'hard-timeout', 'unhandled', 'survived'], 2);
  assert.equal(failures.outcomes.survived.outcome, 'survived');
  assert.equal(failures.events.filter(e => e.type === 'deadline').length, 1);
  assert.equal(failures.events.find(e => e.type === 'deadline').mutant, 'hard-timeout');
  assert.equal(failures.events.find(e => e.type === 'unhandled').mutant, 'unhandled');
  assert.equal(failures.workerFailures.crash, 'crash');
  assert.equal(failures.workerFailures['hard-timeout'], 'timeout');
  assert.equal(failures.workerFailures.unhandled, 'unhandled');
  assert.ok(failures.errors.length >= 3);
  await writeFile(new URL('./research-evidence.json', import.meta.url), JSON.stringify({ duplicate, overlap, simultaneous, failures }, null, 2) + '\n');
});
