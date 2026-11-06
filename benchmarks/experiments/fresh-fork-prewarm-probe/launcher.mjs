import assert from 'node:assert/strict';

const send = process.send?.bind(process), began = performance.now();
assert.ok(send, 'prewarm worker requires Node IPC');
let assigned = false;
// Register before Vitest: its real start handler must see the current selector.
process.on('message', message => {
  if (message?.type !== 'start' || message.__vitest_worker_request__ !== true) return;
  assert.equal(assigned, false, 'fresh worker assigned twice');
  const job = message.oximutAssignment;
  assert.ok(job && job.nonce === process.env.OXIMUT_PREWARM_NONCE, 'missing worker-bound assignment');
  assert.ok(typeof job.label === 'string');
  assert.ok(job.activeMutant === null ||
    typeof job.activeMutant === 'string' && /^[a-f0-9]{64}$/.test(job.activeMutant),
    'malformed active mutant');
  if (job.activeMutant === null) delete process.env.OXIMUT_ACTIVE_MUTANT;
  else process.env.OXIMUT_ACTIVE_MUTANT = job.activeMutant;
  process.env.OXIMUT_PROBE_JOB_ID = job.label;
  assigned = true;
  delete message.oximutAssignment;
});
await import(process.env.OXIMUT_REAL_FORK_ENTRY);
send({ oximutFrameworkImported: true, pid: process.pid,
  nonce: process.env.OXIMUT_PREWARM_NONCE, importDurationMs: performance.now() - began });
