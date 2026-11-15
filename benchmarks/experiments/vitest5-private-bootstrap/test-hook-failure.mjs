import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { SharedLanes } from '../../../packages/vitest-adapter/shared-lanes.ts';

const repo = resolve(import.meta.dirname, '../../..');

for (const stage of ['started', 'dispatched']) test(`a throwing ${stage} experiment hook fails closed and drains its fresh fork`, { timeout: 45000 }, async t => {
  const previousExitCode = process.exitCode;
  const root = await mkdtemp(join(tmpdir(), 'w13-hook-failure-'));
  await symlink(join(repo, 'node_modules'), join(root, 'node_modules'));
  const file = join(root, 'subject.ts');
  const code = 'export const value = 1;';
  await writeFile(file, code);
  await writeFile(join(root, 'vitest.config.mjs'), "export default {test:{include:['case.test.ts']}};");
  await writeFile(join(root, 'case.test.ts'), "import {test,expect} from 'vitest'; import {value} from './subject'; test('value',()=>expect(value).toBe(1));");
  const births = [];
  let inject = true, injected = false;
  const lanes = new SharedLanes({
    entrypoint: '', baseline() {}, validateConfig() {}, configure() {},
    started(worker) {
      const pid = worker.fork.pid;
      const stat = readFileSync(`/proc/${pid}/stat`, 'utf8');
      births.push({ pid, ticks: stat.slice(stat.lastIndexOf(') ') + 2).split(' ')[19] });
      if (inject && stage === 'started') { injected = true; throw new Error('Injected post-start hook failure'); }
    },
    dispatched() {
      if (inject && stage === 'dispatched') { injected = true; throw new Error('Injected pre-send hook failure'); }
    },
  });
  t.after(async () => { try { await lanes.close(); } finally { await rm(root, { recursive: true, force: true }); } });
  const module = { id: 'resident', file, code, map: { version: 3, sources: ['subject.ts'], names: [], mappings: 'AAAA' } };
  const job = { protocol: 1, runId: 'hook-failure', jobId: 'first', generation: 1, kind: 'mutant', root,
    config: join(root, 'vitest.config.mjs'), deadlineMs: 15000, mutation: { ...module, id: 'first' }, resident: [module] };
  let failed;
  try { failed = await lanes.run({ ...job, lanes: [job] }); }
  catch { failed = undefined; }
  assert.equal(injected, true, 'The failure must occur in the actual pre-send hook');
  if (failed) assert.ok(failed.lanes.every(lane => lane.outcome === 'infrastructure-error'), 'Hook failure cannot become test evidence');
  assert.equal(births.length, 1);
  const gone = birth => {
    try {
      const stat = readFileSync(`/proc/${birth.pid}/stat`, 'utf8');
      assert.notEqual(stat.slice(stat.lastIndexOf(') ') + 2).split(' ')[19], birth.ticks, 'Original worker must be gone, including zombies');
    } catch (error) { if (error.code !== 'ENOENT' && error.code !== 'ESRCH') throw error; }
  };
  gone(births[0]); // Check before explicit owner close, not merely after teardown.
  await lanes.close();
  inject = false;
  const next = { ...job, jobId: 'next', mutation: { ...module, id: 'next' } };
  const result = await lanes.run({ ...next, lanes: [next] });
  assert.equal(result.lanes[0].outcome, 'passed');
  assert.equal(births.length, 2);
  assert.notDeepEqual(births[0], births[1], 'Recovery must start a fresh test process');
  births.forEach(gone);
  await lanes.close();
  // Vitest marks the embedding process failed after the deliberately injected
  // infrastructure error. Restore only after every recovery assertion passes.
  process.exitCode = previousExitCode;
});
