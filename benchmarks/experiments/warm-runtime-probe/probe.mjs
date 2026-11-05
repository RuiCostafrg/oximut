// Structural isolation experiment only; local timings are not benchmark evidence.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { appendFile, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const script = fileURLToPath(import.meta.url);
const require = createRequire(import.meta.url);
const testsPerFile = 8;
const timeoutMs = 45000;

async function controller(input) {
  const { createVitest } = await import('vitest/node');
  let context;
  let warm;
  let effectiveConfig;
  const runs = [];
  let last;
  try {
    if (['warmVm', 'guardedVm'].includes(input.pool)) {
      assert.equal(input.schedule, 'separate', 'custom warm hosts support serial separate jobs only');
      assert.equal(input.isolate, true, 'custom warm hosts require a fresh realm per job');
      warm = (await import('./warm-pool.mjs')).createWarmPool({ guarded: input.pool === 'guardedVm' });
    }
    context = await createVitest('test', {
      root: input.root, config: false, watch: false, pool: warm?.pool ?? input.pool,
      maxWorkers: 1, maxConcurrency: 1, fileParallelism: false,
      isolate: input.isolate, coverage: { enabled: false },
      reporters: [{ onTestRunEnd(...args) { last = args; } }],
      server: { deps: { external: ['stateful-cjs', 'stateful-esm'] } },
    });
    await context.standalone();
    assert.equal(context.projects.length, 1);
    effectiveConfig = { pool: context.projects[0].config.pool,
      isolate: context.projects[0].config.isolate };
    const specs = (await context.globTestSpecifications()).sort((a,b) => a.moduleId.localeCompare(b.moduleId));
    assert.equal(specs.length, 2, 'exactly two file specifications must be collected');
    const batches = input.schedule === 'separate' ? specs.map(spec => [spec]) : [specs];
    for (const batch of batches) {
      last = undefined;
      // Deliberately no forced drain between calls: measure stock API reuse,
      // rather than preventing reuse by closing the pool ourselves.
      await context.runTestSpecifications(batch, true);
      if (warm) await context.pool.close(); // Drain the context, park the host.
      assert.ok(last, 'each run must report completion');
      const [modules, unhandled, reason] = last;
      const counts = { passed: 0, failed: 0, skipped: 0 };
      const errors = [...unhandled];
      for (const module of modules) {
        errors.push(...module.errors());
        for (const suite of module.children.allSuites()) errors.push(...suite.errors());
        for (const test of module.children.allTests()) {
          const result = test.result();
          assert.ok(Object.hasOwn(counts, result.state), 'no pending/unexecuted tests');
          counts[result.state]++;
        }
      }
      assert.equal(modules.length, batch.length, 'no missing test files');
      assert.equal(counts.passed + counts.failed, testsPerFile * batch.length, 'exact test inventory');
      assert.equal(counts.skipped, 0);
      assert.equal(errors.length, 0, `no collection/unhandled errors: ${errors.map(e=>e.message)}`);
      assert.notEqual(reason, 'interrupted');
      runs.push({ files: batch.map(s=>s.moduleId), counts });
    }
  } finally {
    try { if (context) await context.close(); }
    finally { await warm?.dispose(); } // Terminate even on Vitest close failure.
  }
  await writeFile(input.result, JSON.stringify({ runs, effectiveConfig, closed: true, guard: warm?.evidence ?? [] }) + '\n');
}

function groupExists(pid) {
  try { process.kill(-pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; throw error; }
}
function killGroup(pid) {
  try { process.kill(-pid, 'SIGKILL'); }
  catch (error) { if (error.code !== 'ESRCH') throw error; }
}
async function runController(input, folder) {
  const file = join(folder, 'input.json');
  await writeFile(file, JSON.stringify(input));
  const child = spawn(process.execPath, [script, '--controller', file], {
    detached: true, env: { ...process.env, NODE_OPTIONS: '' }, stdio: ['ignore','pipe','pipe'],
  });
  let stdout = '', stderr = '', timedOut = false;
  child.stdout.on('data', b => { stdout += b; });
  child.stderr.on('data', b => { stderr += b; });
  const timer = setTimeout(() => { timedOut = true; if (child.pid) killGroup(child.pid); }, timeoutMs);
  let status;
  try {
    status = await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('close', (code, signal) => resolve({ code, signal }));
    });
  } finally { clearTimeout(timer); }
  await writeFile(join(folder, 'stdout.log'), stdout);
  await writeFile(join(folder, 'stderr.log'), stderr);
  if (child.pid && groupExists(child.pid)) {
    killGroup(child.pid);
    for (let i=0; i<20 && groupExists(child.pid); i++) await delay(25);
    throw new Error(`controller left a live process group: ${child.pid}`);
  }
  assert.equal(timedOut, false, `controller exceeded ${timeoutMs}ms`);
  assert.equal(status.signal, null, `controller terminated: ${status.signal}`);
  assert.ok([0,1].includes(status.code), `controller failed: ${stderr.slice(-3000)}`);
  const result = JSON.parse(await readFile(input.result, 'utf8'));
  assert.equal(result.closed, true);
  // Vitest sets exitCode=1 for deliberately failing isolation controls. Retain
  // that status, and accept it only with a complete/drained result and failures.
  if (status.code === 1) assert.ok(result.runs.some(r=>r.counts.failed > 0));
  return { ...result, controllerExitCode: status.code };
}

async function fixtures(root, observationFile, fixtureMode = 'legacy') {
  await mkdir(join(root, 'node_modules'), { recursive: true });
  const vitestRoot = dirname(require.resolve('vitest/package.json'));
  await symlink(vitestRoot, join(root, 'node_modules/vitest'), 'dir');
  await writeFile(join(root, 'package.json'), '{"type":"module"}\n');
  for (const [name, type, source] of [
    ['stateful-cjs','commonjs', 'module.exports = { value: 0 };'],
    ['stateful-esm','module', 'export default { value: 0 };'],
  ]) {
    const pkg = join(root, 'node_modules', name);
    await mkdir(pkg);
    await writeFile(join(pkg,'package.json'), JSON.stringify({name,type,main:'index.js'}));
    await writeFile(join(pkg,'index.js'), source);
  }
  await writeFile(join(root,'local.mjs'), 'export default { value: 0 };\n');
  for (const file of ['a','b']) await writeFile(join(root, `${file}.test.mjs`), `
import { test, expect, beforeAll, afterAll, vi } from 'vitest';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import { threadId } from 'node:worker_threads';
import local from './local.mjs';
import cjs from 'stateful-cjs';
import esm from 'stateful-esm';
const spyTarget = globalThis.__oximutSpyTarget ??= { call() { return 'original'; } };
const realm = globalThis.__oximutRealm ??= randomUUID();
beforeAll(() => fs.appendFileSync(${JSON.stringify(observationFile)}, JSON.stringify({
  file:${JSON.stringify(file)}, pid:process.pid, threadId, realm,
  local:local.value, cjs:cjs.value, esm:esm.value,
  global:globalThis.__oximutGlobal ?? null,
  env:process.env.OXIMUT_WARM_STATE ?? null, builtin:fs.__oximutWarmState ?? null,
  spy:spyTarget.call(), fakeClock:Date.now() === 1234,
}) + '\\n'));
test('local ESM starts fresh', () => expect(local.value).toBe(0));
test('external CJS starts fresh', () => expect(cjs.value).toBe(0));
test('external ESM starts fresh', () => expect(esm.value).toBe(0));
test('global starts fresh', () => expect(globalThis.__oximutGlobal).toBeUndefined());
test('environment starts fresh', () => expect(process.env.OXIMUT_WARM_STATE).toBeUndefined());
test('builtin object starts fresh', () => expect(fs.__oximutWarmState).toBeUndefined());
test('spy starts fresh', () => expect(spyTarget.call()).toBe('original'));
test('real timers start fresh', () => expect(Date.now()).not.toBe(1234));
afterAll(() => {
  local.value++; cjs.value++; esm.value++;
  globalThis.__oximutGlobal = 'dirty';
  if (!['module-only', 'fs-only'].includes(${JSON.stringify(fixtureMode)})) process.env.OXIMUT_WARM_STATE = 'dirty';
  if (!['module-only', 'env-only'].includes(${JSON.stringify(fixtureMode)})) fs.__oximutWarmState = 'dirty';
  vi.spyOn(spyTarget, 'call').mockReturnValue('dirty');
  vi.useFakeTimers(); vi.setSystemTime(1234);
});
`);
}

async function main() {
  const version = require('vitest/package.json').version;
  assert.equal(version, '4.1.12');
  const args = process.argv.slice(2);
  const outputIndex = args.indexOf('--output');
  const output = outputIndex < 0 ? await mkdtemp(join(tmpdir(),'oximut-warm-evidence-')) : resolve(args[outputIndex+1]);
  if (outputIndex >= 0) await mkdir(output); // Reject stale evidence directories.
  const filterIndex = args.indexOf('--case');
  const filter = filterIndex < 0 ? undefined : args[filterIndex+1];
  const modeIndex = args.indexOf('--mode');
  const selectedMode = modeIndex < 0 ? 'host-dirty' : args[modeIndex+1];
  assert.ok(['module-only', 'env-only', 'fs-only', 'host-dirty'].includes(selectedMode), 'unknown fixture mode');
  const results = [];
  const customPool = ['warmVm', 'guardedVm'].find(pool => filter?.startsWith(`${pool}-`));
  const pools = customPool ? [customPool] : ['forks','vmForks','vmThreads'];
  for (const pool of pools) for (const isolate of [true,false]) {
    for (const schedule of ['separate','batch']) {
      const name = `${pool}-${isolate}-${schedule}`;
      if (filter && filter !== name) continue;
      const folder = join(output, name);
      await mkdir(folder);
      const root = join(folder,'project');
      const observations = join(folder,'observations.jsonl');
      try {
        const fixtureMode = selectedMode;
        await fixtures(root, observations, fixtureMode);
        const input = { pool,isolate,schedule,root,fixtureMode,result:join(folder,'result.json') };
        const result = await runController(input, folder);
        const events = (await readFile(observations,'utf8')).trim().split('\n').map(JSON.parse);
        assert.equal(events.length, 2);
        assert.deepEqual(events.map(e=>e.file).sort(), ['a','b']);
        const identities = events.map(e=>`${e.pid}:${e.threadId}`);
        const counts = result.runs.reduce((sum,r) => ({ passed:sum.passed+r.counts.passed, failed:sum.failed+r.counts.failed }),{passed:0,failed:0});
        assert.equal(counts.passed + counts.failed, 2 * testsPerFile);
        // Reused stock batches are deliberate negative controls: they share a
        // realm as well as state. Separate calls and the custom VM must not.
        if (schedule === 'separate' || (pool === 'forks' && isolate)) {
          assert.notEqual(events[0].realm, events[1].realm, 'isolated jobs must have distinct realms');
        }
        if (pool === 'guardedVm') {
          assert.equal(result.guard.length, 2, 'guard evidence required for each stopped job');
          const expectDirty = fixtureMode !== 'module-only';
          assert.ok(result.guard.every(g => g.available && g.dirty === expectDirty), 'guard decision must match fixture mutations');
          const expectedEnv = ['env-only','host-dirty'].includes(fixtureMode) ? ['OXIMUT_WARM_STATE'] : [];
          const expectedFs = ['fs-only','host-dirty'].includes(fixtureMode) ? ['__oximutWarmState'] : [];
          for (let i = 0; i < 2; i++) {
            assert.equal(`${result.guard[i].pid}:${result.guard[i].threadId}`, identities[i], 'guard identity must match observed worker');
            assert.deepEqual(result.guard[i].envChanges, expectedEnv, 'exact changed environment keys');
            assert.deepEqual(result.guard[i].fsChanges, expectedFs, 'exact changed builtin descriptors');
          }
          assert.equal(identities[0] === identities[1], !expectDirty, 'clean host must be reused; dirty host must be recycled');
        }
        const fresh = counts.failed === 0;
        if ((pool === 'forks' && isolate) || pool === 'guardedVm') {
          assert.ok(fresh, 'fresh-fork oracle and guarded fixtures must pass every assertion');
        }
        results.push({ name,pool,isolate,schedule,fixtureMode,controllerExitCode:result.controllerExitCode,
          effectiveConfig:result.effectiveConfig, counts,fresh,
          reusedWorker:identities[0]===identities[1],guard:result.guard ?? [],events });
        await appendFile(join(output,'results.jsonl'), JSON.stringify(results.at(-1))+'\n');
        console.log(JSON.stringify(results.at(-1)));
      } finally {
        await rm(root,{recursive:true,force:true});
      }
    }
  }
  assert.ok(results.length, 'no matching cases');
  const sourceHashes = {};
  for (const source of [script, fileURLToPath(new URL('./warm-pool.mjs', import.meta.url)), fileURLToPath(new URL('./warm-vm-worker.mjs', import.meta.url))]) {
    sourceHashes[source.slice(dirname(dirname(dirname(script))).length + 1)] = createHash('sha256').update(await readFile(source)).digest('hex');
  }
  await writeFile(join(output,'metadata.json'), JSON.stringify({version,node:process.version,platform:process.platform,arch:process.arch,output,command:process.argv.join(' '),sourceHashes,cleanup:{controllerProcessGroupGone:true,vitestClosed:true},measurement:'structural only; wall/CPU/RSS/process-tree/phase ratios/CIs are unmeasured/N/A'},null,2)+'\n');
  console.log(`Evidence: ${output}`);
}

if (process.argv[2] === '--controller') await controller(JSON.parse(await readFile(process.argv[3],'utf8')));
else await main();
