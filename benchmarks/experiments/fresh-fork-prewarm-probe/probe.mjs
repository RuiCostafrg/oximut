// A standalone diagnostic prototype, never an Oximut/Stryker qualification.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, symlink, unlink, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const script = fileURLToPath(import.meta.url), require = createRequire(import.meta.url);
export const jobs = [
  { label: 'baseline', activeMutant: null },
  { label: 'mutant-alpha', activeMutant: 'a'.repeat(64) },
  { label: 'null', activeMutant: null },
  { label: 'mutant-beta', activeMutant: 'b'.repeat(64) },
];
const names = ['builtin clean', 'env clean', 'external CJS clean', 'external ESM clean',
  'global clean', 'local ESM clean', 'spy clean', 'timer clean'];
const timeoutMs = 45_000, hash = data => createHash('sha256').update(data).digest('hex');
const metrics = Object.fromEntries(['wholeCommandWallMs', 'cpuUserMs', 'cpuSysMs', 'treeCpuMs',
  'aggregateRssBytes', 'peakAggregateProcessCount', 'observedAggregateProcessCount',
  'resources.phaseWallMs', 'pairedOximutStrykerRatios', 'bootstrap95CIs']
  .map(key => [key, 'unmeasured/N/A; standalone structural fixture']));

async function rows(file) {
  return (await readFile(file, 'utf8')).split('\n').filter(Boolean).map(JSON.parse);
}

/** Validate independent job, worker, setup-file, top-level and state observations. */
export function validateObservations(observed, executed, expected = jobs) {
  assert.equal(observed.length, expected.length * 3, 'exact observation inventory');
  assert.equal(executed.length, expected.length, 'exact worker inventory');
  for (let i = 0; i < expected.length; i++) {
    const group = observed.slice(i * 3, i * 3 + 3), worker = executed[i];
    assert.deepEqual(group.map(row => row.phase), ['setup-file', 'module', 'state']);
    for (const row of group) {
      assert.equal(row.requestedId, expected[i].label, 'independent requested job mismatch');
      assert.equal(row.activeMutant, expected[i].activeMutant, 'independent active mutant mismatch');
      assert.equal(row.pid, worker.pid, 'observation/worker PID mismatch');
      assert.equal(row.realm, group[0].realm, 'setup/module realm mismatch');
      assert.match(row.realm, /^[0-9a-f-]{36}$/);
    }
    assert.equal(worker.requestedId, expected[i].label);
    assert.ok(Number.isSafeInteger(worker.pid) && worker.pid > 0);
    assert.match(worker.startTime, /^\d+$/);
    const state = group[2];
    for (const [key, value] of Object.entries({ local: 0, cjs: 0, esm: 0, global: null,
      env: null, builtin: null, spy: 'original', fakeClock: false }))
      assert.equal(state[key], value, 'dirty or missing state: ' + key);
  }
  assert.equal(new Set(executed.map(job => job.pid)).size, expected.length, 'fresh PID per job');
  assert.equal(new Set(executed.map(job => job.pid + ':' + job.startTime)).size, expected.length);
  assert.equal(new Set(observed.filter(row => row.phase === 'module').map(row => row.realm)).size,
    expected.length, 'fresh realm per job');
}

/** Signal/timeout/unrelated assertion failures cannot certify the stale-ID control. */
export function validateControllerStatus(status, result, expectedFailure = false) {
  assert.equal(status.spawnError ?? null, null, 'controller spawn failed');
  assert.equal(status.interrupted ?? false, false, 'controller was interrupted');
  assert.equal(status.timedOut, false, 'controller timed out');
  assert.equal(status.cleanupRequired, false, 'forced cleanup disqualifies controller');
  assert.equal(status.cleanupVerified, true, 'private process group remains');
  assert.equal(status.status.signal, null, 'controller terminated by signal');
  assert.equal(result.closed, true, 'Vitest close did not finish');
  assert.equal(result.disposed, true, 'spare disposal did not finish');
  assert.deepEqual(result.cleanupErrors, [], 'controller cleanup errors');
  if (expectedFailure) {
    assert.equal(status.status.code, 1, 'counterfactual must exit exactly 1');
    assert.equal(result.failureStage, 'selector-oracle', 'unrelated failure is not selector evidence');
    assert.equal(result.observedSelector, null);
    assert.equal(result.expectedSelector, jobs[1].activeMutant);
  } else {
    assert.equal(status.status.code, 0, 'controller failed');
    assert.equal(result.failure, undefined);
    assert.equal(result.freshnessAndHandoff, true);
  }
}

async function controller(input) {
  const result = { mode: input.mode, requested: jobs, jobs: [], runs: [],
    controllerPid: process.pid, closed: false, disposed: false, cleanupErrors: [], stages: {} };
  let context, pool, reporterEnd;
  try {
    const began = performance.now();
    const { createVitest } = await import('vitest/node');
    result.stages.vitestImportMs = performance.now() - began;
    const poolStart = performance.now();
    const { createForkPool } = await import('./pool.mjs');
    result.stages.poolImportMs = performance.now() - poolStart;
    pool = createForkPool({ launcher: input.mode === 'stock' ? undefined :
      fileURLToPath(new URL('./launcher.mjs', import.meta.url)), observationFile: input.observations });
    const init = performance.now();
    context = await createVitest('test', {
      root: input.root, config: false, watch: false, pool: pool.pool, setupFiles: [join(input.root, 'setup.mjs')],
      maxWorkers: 1, maxConcurrency: 1, fileParallelism: false, isolate: true,
      coverage: { enabled: false }, reporters: [{ onTestRunEnd(...args) { reporterEnd = args; } }],
      server: { deps: { external: ['stateful-cjs', 'stateful-esm'] } },
    });
    await context.standalone();
    result.stages.initializeMs = performance.now() - init;
    assert.equal(context.projects.length, 1);
    const config = context.projects[0].config;
    assert.equal(config.isolate, true); assert.equal(config.maxWorkers, 1);
    assert.equal(config.maxConcurrency, 1); assert.equal(config.environment, 'node');
    const discover = performance.now(), specs = await context.globTestSpecifications();
    result.stages.discoveryMs = performance.now() - discover;
    assert.equal(specs.length, 1, 'exactly one structural spec');
    for (const job of jobs) {
      reporterEnd = undefined;
      pool.setNext({ ...job, corrupt: input.corruptHandoff && job.label === jobs[1].label });
      if (job.activeMutant === null) delete process.env.OXIMUT_ACTIVE_MUTANT;
      else process.env.OXIMUT_ACTIVE_MUTANT = job.activeMutant;
      process.env.OXIMUT_PROBE_JOB_ID = job.label;
      const begin = performance.now();
      await context.runTestSpecifications([specs[0]], true);
      const resolved = performance.now();
      await context.pool.close();
      const finished = performance.now();
      assert.ok(reporterEnd, 'missing onTestRunEnd');
      const [modules, unhandled, reason] = reporterEnd;
      const errors = [...unhandled, ...modules.flatMap(module =>
        [...module.errors(), ...[...module.children.allSuites()].flatMap(suite => suite.errors())])];
      assert.equal(modules.length, 1); assert.deepEqual(errors, []); assert.notEqual(reason, 'interrupted');
      const tests = modules.flatMap(module => [...module.children.allTests()]
        .map(test => ({ name: test.name, state: test.result().state }))).sort((a, b) => a.name.localeCompare(b.name));
      assert.deepEqual(tests, names.map(name => ({ name, state: 'passed' })));
      const worker = pool.workers.at(-1).evidence;
      const prior = result.jobs.at(-1);
      result.jobs.push({ ...worker, commandStartedAt: begin, runResolvedAt: resolved, drainedAt: finished,
        runCallMs: resolved - begin, drainMs: finished - resolved,
        startupOverlapWithPriorRunMs: prior && worker.readyReceivedAt != null ? Math.max(0,
          Math.min(worker.readyReceivedAt, prior.runResponseAt) -
          Math.max(worker.spawnStartedAt, prior.runSentAt)) : null,
        readyBeforePriorRunResponse: prior && worker.readyReceivedAt != null ?
          worker.readyReceivedAt < prior.runResponseAt : null,
        readyBeforePriorDrain: prior && worker.readyReceivedAt != null ?
          worker.readyReceivedAt < prior.drainedAt : null,
      });
      result.runs.push({ requestedId: job.label, tests });
      const observed = await rows(input.observations);
      const last = observed.at(-1);
      if (last.activeMutant !== job.activeMutant) {
        result.failureStage = 'selector-oracle';
        result.observedSelector = last.activeMutant;
        result.expectedSelector = job.activeMutant;
        throw new Error('independent active mutant mismatch');
      }
      validateObservations(observed, result.jobs, jobs.slice(0, result.jobs.length));
    }
    result.observations = await rows(input.observations);
    result.freshnessAndHandoff = true;
  } catch (error) {
    result.failure = String(error.stack || error); process.exitCode = 1;
  } finally {
    delete process.env.OXIMUT_ACTIVE_MUTANT; delete process.env.OXIMUT_PROBE_JOB_ID;
    const close = performance.now();
    try { if (context) await context.close(); result.closed = Boolean(context); }
    catch (error) { result.cleanupErrors.push(String(error)); process.exitCode = 1; }
    result.stages.closeMs = performance.now() - close;
    const dispose = performance.now();
    try { if (pool) { await pool.dispose(); pool.assertDrained(); result.disposed = true; } }
    catch (error) { result.cleanupErrors.push(String(error)); process.exitCode = 1; }
    result.stages.spareDisposalMs = performance.now() - dispose;
    result.workerEvidence = pool?.evidence ?? [];
    await writeFile(input.result, JSON.stringify(result, null, 2) + '\n');
    if (process.connected) process.disconnect();
  }
}

function groupExists(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  try { process.kill(-pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; throw error; }
}
function killGroup(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return;
  try { process.kill(-pid, 'SIGKILL'); }
  catch (error) { if (error.code !== 'ESRCH') throw error; }
}

async function invoke(input, folder) {
  const inputPath = join(folder, 'input.json');
  await writeFile(inputPath, JSON.stringify(input));
  const child = spawn(process.execPath, [script, '--controller', inputPath], {
    detached: true, env: { ...process.env, NODE_OPTIONS: '', OXIMUT_PREWARM_OBSERVATIONS: input.observations },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  let stdout = '', stderr = '', timedOut = false, interrupted = false, spawnError = null, status;
  child.stdout.on('data', data => { stdout = (stdout + data).slice(-65536); });
  child.stderr.on('data', data => { stderr = (stderr + data).slice(-65536); });
  const timer = setTimeout(() => { timedOut = true; if (child.pid) killGroup(child.pid); }, timeoutMs);
  const interrupt = () => { interrupted = true; killGroup(child.pid); };
  process.once('SIGINT', interrupt); process.once('SIGTERM', interrupt);
  try {
    status = await new Promise((resolve, reject) => {
      child.once('error', reject); child.once('close', (code, signal) => resolve({ code, signal }));
    });
  } catch (error) { spawnError = String(error); }
  finally {
    clearTimeout(timer);
    process.off('SIGINT', interrupt); process.off('SIGTERM', interrupt);
  }
  const cleanupRequired = groupExists(child.pid);
  if (cleanupRequired) {
    killGroup(child.pid);
    for (let i = 0; i < 20 && groupExists(child.pid); i++) await delay(25);
  }
  const record = { status, timedOut, interrupted, spawnError, cleanupRequired, cleanupVerified: !groupExists(child.pid),
    deadlineMs: timeoutMs, logs: 'bounded 64 KiB tails, per stream' };
  await writeFile(join(folder, 'stdout.log'), stdout); await writeFile(join(folder, 'stderr.log'), stderr);
  await writeFile(join(folder, 'controller-status.json'), JSON.stringify(record, null, 2) + '\n');
  assert.equal(spawnError, null, 'controller spawn failed: ' + spawnError);
  const result = JSON.parse(await readFile(input.result, 'utf8'));
  validateControllerStatus(record, result, input.corruptHandoff);
  return { ...result, controllerStatus: record };
}

async function fixtures(root) {
  await mkdir(join(root, 'node_modules'), { recursive: true });
  await symlink(dirname(require.resolve('vitest/package.json')), join(root, 'node_modules/vitest'), 'dir');
  await writeFile(join(root, 'package.json'), '{"type":"module"}\n');
  await writeFile(join(root, 'local.mjs'), 'export default {value:0};\n');
  for (const [name, type, text] of [['stateful-cjs', 'commonjs', 'module.exports={value:0};'],
    ['stateful-esm', 'module', 'export default {value:0};']]) {
    const folder = join(root, 'node_modules', name); await mkdir(folder);
    await writeFile(join(folder, 'package.json'), JSON.stringify({ name, type, main: 'index.js' }));
    await writeFile(join(folder, 'index.js'), text);
  }
  for (const name of ['fixture.test.mjs', 'setup.mjs'])
    await writeFile(join(root, name), await readFile(new URL('./fixtures/' + name, import.meta.url)));
  const files = ['package.json', 'local.mjs', 'fixture.test.mjs', 'setup.mjs',
    ...['stateful-cjs', 'stateful-esm'].flatMap(name =>
      ['package.json', 'index.js'].map(file => 'node_modules/' + name + '/' + file))];
  return Object.fromEntries(await Promise.all(files.map(async file =>
    [file, hash(await readFile(join(root, file)))])));
}

export function parseArguments(args) {
  const parsed = { mode: 'matrix' }, seen = new Set();
  for (let i = 0; i < args.length; i += 2) {
    assert.ok(['--output', '--case'].includes(args[i]) && args[i + 1] &&
      !args[i + 1].startsWith('--'), 'expected --output NEW_DIRECTORY or --case stock|prewarm|matrix');
    const key = args[i] === '--output' ? 'output' : 'mode';
    assert.ok(!seen.has(key), 'duplicate option');
    seen.add(key);
    parsed[key] = args[i + 1];
  }
  assert.ok(['matrix', 'stock', 'prewarm'].includes(parsed.mode), 'unknown probe case');
  return parsed;
}

async function main(args) {
  const parsed = parseArguments(args);
  const output = parsed.output ? resolve(parsed.output) : await mkdtemp(join(tmpdir(), 'fresh-fork-prewarm-'));
  if (parsed.output) await mkdir(output);
  const evidence = { formatVersion: 2, command: process.argv, node: process.version,
    platform: process.platform, arch: process.arch, metrics, cases: [], structuralOracle: false };
  try {
    evidence.vitest = JSON.parse(await readFile(require.resolve('vitest/package.json'), 'utf8')).version;
    assert.equal(evidence.vitest, '4.1.12');
    evidence.packageLockSha256 = hash(await readFile(new URL('../../../package-lock.json', import.meta.url)));
    const inputs = ['probe.mjs', 'pool.mjs', 'launcher.mjs', 'test.mjs',
      'measure.py', 'test_measurement.py', 'fixtures/setup.mjs', 'fixtures/fixture.test.mjs'];
    evidence.sourceHashes = Object.fromEntries(await Promise.all(inputs.map(async file =>
      [file, hash(await readFile(new URL(file, import.meta.url)))])));
    const vitestRoot = dirname(require.resolve('vitest/package.json'));
    const api = await readFile(join(vitestRoot, 'dist/node.js'));
    const chunk = api.toString().match(/\.\/chunks\/(cli-api\.[A-Za-z0-9_-]+\.js)/)?.[1];
    assert.ok(chunk);
    evidence.runtimeHashes = { nodeApi: hash(api),
      poolRunner: hash(await readFile(join(vitestRoot, 'dist/chunks', chunk))),
      realForkEntry: hash(await readFile(join(vitestRoot, 'dist/workers/forks.js'))) };
    const modes = parsed.mode === 'matrix' ? ['stock', 'prewarm', 'stale-handoff'] : [parsed.mode];
    for (const mode of modes) {
      const folder = join(output, mode), root = join(folder, 'project');
      await mkdir(folder);
      try {
        const fixtureHashes = await fixtures(root);
        const result = await invoke({ mode: mode === 'stock' ? 'stock' : 'prewarm',
          corruptHandoff: mode === 'stale-handoff', root, observations: join(folder, 'observations.jsonl'),
          result: join(folder, 'result.json') }, folder);
        if (mode === 'stale-handoff') evidence.staleHandoffCounterfactual = result;
        else evidence.cases.push({ ...result, fixtureHashes });
      } finally {
        await unlink(join(root, 'node_modules/vitest')).catch(error => {
          if (error.code !== 'ENOENT') throw error;
        });
      }
    }
    if (parsed.mode === 'matrix') {
      assert.deepEqual(evidence.cases[0].fixtureHashes, evidence.cases[1].fixtureHashes);
      assert.deepEqual(evidence.cases[0].runs, evidence.cases[1].runs);
      evidence.structuralOracle = true;
    }
    evidence.selectedCasesPassed = true;
  } catch (error) { evidence.failure = String(error.stack || error); process.exitCode = 1; }
  finally { await writeFile(join(output, 'evidence.json'), JSON.stringify(evidence, null, 2) + '\n'); }
}

if (process.argv[1] && resolve(process.argv[1]) === script) {
  if (process.argv[2] === '--controller') await controller(JSON.parse(await readFile(process.argv[3], 'utf8')));
  else await main(process.argv.slice(2));
}
