// Assertions about independent observations, not a product isolation guarantee.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export function validateControllerStatus(status, runs) {
  assert.equal(status.signal, null, 'controller must exit normally');
  const failed = runs.some(run => run.counts.failed > 0);
  assert.equal(status.code, failed ? 1 : 0, 'exit code must agree with assertion failures');
}

export function validateCase(row) {
  const { pool, isolate, schedule, fixtureMode, events } = row;
  assert.ok(['forks','vmForks','vmThreads','warmVm','guardedVm'].includes(pool));
  assert.equal(typeof isolate, 'boolean');
  assert.ok(['batch','separate'].includes(schedule));
  assert.ok(['module-only','env-only','fs-only','host-dirty'].includes(fixtureMode));
  assert.equal(row.name, `${pool}-${isolate}-${schedule}`);
  assert.equal(events.length, 2, 'exact observation inventory');
  assert.deepEqual(events.map(e => e.file).sort(), ['a','b']);
  const identities = events.map(event => {
    assert.ok(Number.isSafeInteger(event.pid) && event.pid > 0);
    assert.ok(Number.isSafeInteger(event.threadId) && event.threadId >= 0);
    assert.match(event.realm, /^[0-9a-f-]{36}$/);
    return `${event.pid}:${event.threadId}`;
  });
  const custom = pool === 'warmVm' || pool === 'guardedVm';
  assert.deepEqual(row.effectiveConfig, {
    pool: custom ? 'oximut-warm-vm-probe' : pool,
    isolate: pool === 'vmForks' || pool === 'vmThreads' ? false : isolate,
  }, 'effective pinned pool/isolation must match the declared case');
  if (custom) {
    assert.equal(isolate, true);
    assert.equal(schedule, 'separate');
  }
  const stockShared = !custom && schedule === 'batch' && !(pool === 'forks' && isolate);
  const envDirty = ['env-only','host-dirty'].includes(fixtureMode);
  const fsDirty = ['fs-only','host-dirty'].includes(fixtureMode);
  const reused = stockShared || pool === 'warmVm' || (pool === 'guardedVm' && !envDirty && !fsDirty);
  assert.equal(identities[0] === identities[1], reused, 'expected worker identity relationship');
  assert.equal(row.reusedWorker, reused);
  assert.equal(events[0].realm === events[1].realm, stockShared, 'expected realm identity relationship');
  const clean = { local:0, cjs:0, esm:0, global:null, env:null, builtin:null, spy:'original', fakeClock:false };
  const second = { ...clean };
  if (stockShared) Object.assign(second, { local:1, cjs:1, esm:1, global:'dirty', fakeClock:true });
  if (stockShared || pool === 'warmVm') {
    if (envDirty) second.env = 'dirty';
    if (fsDirty) second.builtin = 'dirty';
  }
  for (const [index, expected] of [clean, second].entries()) {
    for (const [key, value] of Object.entries(expected)) {
      assert.equal(events[index][key], value, `observed ${key} state in job ${index + 1}`);
    }
  }
  const failures = (stockShared ? 5 : 0) + ((stockShared || pool === 'warmVm') ? Number(envDirty) + Number(fsDirty) : 0);
  assert.deepEqual(row.counts, { passed:16-failures, failed:failures }, 'exact intended assertion outcomes');
  assert.equal(row.fresh, failures === 0);
  assert.equal(row.controllerExitCode, failures > 0 ? 1 : 0);
  if (pool === 'guardedVm') {
    assert.equal(row.guard.length, 2, 'one guard per stopped job');
    for (let i=0; i<2; i++) {
      const guard = row.guard[i];
      assert.equal(guard.available, true);
      assert.equal(guard.dirty, envDirty || fsDirty);
      assert.equal(`${guard.pid}:${guard.threadId}`, identities[i]);
      assert.deepEqual(guard.envChanges, envDirty ? ['OXIMUT_WARM_STATE'] : []);
      assert.deepEqual(guard.fsChanges, fsDirty ? ['__oximutWarmState'] : []);
    }
  }
}

export function validateBatch(rows, name) {
  let expected;
  if (name === 'stock') {
    expected = ['forks','vmForks','vmThreads'].flatMap(pool => [true,false].flatMap(isolate =>
      ['separate','batch'].map(schedule => [`${pool}-${isolate}-${schedule}`, 'host-dirty'])));
  } else if (name === 'raw-warm') expected = [['warmVm-true-separate', 'host-dirty']];
  else {
    const match = /^(guard|fork)-(module-only|env-only|fs-only|host-dirty)$/.exec(name);
    assert.ok(match, 'unknown matrix case');
    expected = [[`${match[1] === 'guard' ? 'guardedVm' : 'forks'}-true-separate`, match[2]]];
  }
  assert.deepEqual(rows.map(row => [row.name,row.fixtureMode]).sort(), expected.sort(), 'exact scenario inventory');
  rows.forEach(validateCase);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  assert.equal(process.argv.length, 4, 'usage: validation.mjs CASE RESULTS.jsonl');
  const rows = (await readFile(process.argv[3], 'utf8')).trim().split('\n').map(JSON.parse);
  validateBatch(rows, process.argv[2]);
}
