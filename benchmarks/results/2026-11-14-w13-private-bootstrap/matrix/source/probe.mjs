import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, readFile, writeFile, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { buildCandidate, checkVersions, pins, hash, manifest, require, repo } from './build.mjs';
import { makeFixture, inventory, jobs } from './fixtures.mjs';

const script = fileURLToPath(import.meta.url);
const scenarios = ['sequence', 'throw', 'import-throw', 'deadline'];
const filesFor = scenario => scenario === 'sequence' ? 'state.test.ts' : scenario + '.test.ts';

// Linux identities include birth ticks; never signal a PID based on its number alone.
export async function processGroup(group) {
  const rows = [];
  for (const name of await readdir('/proc')) {
    if (!/^\d+$/.test(name)) continue;
    try {
      const stat = await readFile(`/proc/${name}/stat`, 'utf8');
      const fields = stat.slice(stat.lastIndexOf(') ') + 2).split(' ');
      if (Number(fields[2]) === group) rows.push({ pid: Number(name), state: fields[0], ppid: Number(fields[1]), group: Number(fields[2]), startTime: fields[19] });
    } catch (error) { if (!['ENOENT', 'ESRCH'].includes(error.code)) throw error; }
  }
  return rows;
}

async function controller(input) {
  const { createVitest, ForksPoolWorker } = await import('vitest/node');
  const workers = [], reports = [];
  const context = await createVitest('test', {
    root: input.root, config: false, watch: false, isolate: true, fsModuleCache: false,
    maxWorkers: 1, maxConcurrency: 1, fileParallelism: false, bail: 0,
    testTimeout: 1000, hookTimeout: 1000, setupFiles: [join(input.root, 'setup.ts')],
    coverage: { enabled: false }, server: { deps: { external: [/\/external\//] } },
    pool: { name: 'private-bootstrap-structural', createPoolWorker(options) {
      const worker = new ForksPoolWorker(options), record = {};
      const start = worker.start.bind(worker), stop = worker.stop.bind(worker);
      workers.push(record);
      if (input.bundle) worker.entrypoint = input.bundle;
      worker.start = async () => {
        await start();
        const child = worker.fork;
        const identity = (await processGroup(process.pid)).find(row => row.pid === child.pid);
        assert.ok(identity && identity.ppid === process.pid, 'Real direct OS child required');
        Object.assign(record, identity, { entrypoint: worker.entrypoint });
        await writeFile(input.workers, JSON.stringify(workers, null, 2) + '\n');
      };
      worker.stop = async () => {
        const child = worker.fork;
        await stop();
        Object.assign(record, { exitCode: child.exitCode, signal: child.signalCode });
        assert.ok(child.exitCode === 0 || child.signalCode === 'SIGTERM', 'Unexpected worker exit');
      };
      return worker;
    } },
    reporters: [{ onTestRunEnd(modules, errors, reason) {
      reports.push({ reason, errors: [...errors, ...modules.flatMap(m => [...m.errors(), ...[...m.children.allSuites()].flatMap(s => [...s.errors()])])].map(e => ({ message: e.message, stack: e.stack })), tests: modules.flatMap(m => [...m.children.allTests()].map(t => ({ name: t.name, state: t.result().state }))), testErrors: modules.flatMap(m => [...m.children.allTests()].flatMap(t => (t.result().errors || []).map(e => ({ name: t.name, message: e.message, stack: e.stack })))) });
    } }],
  });
  try {
    await context.standalone();
    const specs = (await context.globTestSpecifications()).filter(spec => spec.moduleId.endsWith('/' + filesFor(input.scenario)));
    assert.equal(specs.length, 1, 'Exact selected test specification required');
    for (const selector of input.scenario === 'sequence' ? jobs : ['inactive']) {
      context.projects[0].config.env = { ...context.projects[0].config.env, OXIMUT_ACTIVE_MUTANT: selector, PROBE_SCENARIO: input.scenario };
      await context.runTestSpecifications(specs, true);
      await context.pool.close();
      await writeFile(input.result, JSON.stringify({ workers, reports }, null, 2) + '\n');
      // Validate each job before admitting the next. Expected mutant kills are failures.
      validateReport(reports.at(-1), input.scenario, selector);
    }
  } finally { await context.close(); }
  await writeFile(input.result, JSON.stringify({ workers, reports }, null, 2) + '\n');
}

export function validateReport(report, scenario, selector) {
  assert.ok(report, 'Missing reporter completion');
  assert.notEqual(report.reason, 'interrupted', 'Unexpected cancellation');
  if (scenario === 'sequence') {
    assert.deepEqual(report.errors, [], 'Unexpected module/unhandled errors');
    assert.deepEqual(report.tests.map(t => t.name), inventory, 'Inventory diverged');
    const killed = selector === 'A' ? 'arithmetic add' : selector === 'B' ? 'arithmetic multiply' : undefined;
    assert.deepEqual(report.tests, inventory.map(name => ({ name, state: name === killed ? 'failed' : 'passed' })), `Selector-specific outcome diverged: ${selector}`);
    assert.equal(report.reason, killed ? 'failed' : 'passed');
    assert.equal(report.testErrors.length, killed ? 1 : 0);
    if (killed) {
      assert.equal(report.testErrors[0].name, killed);
      assert.match(report.testErrors[0].message, selector === 'A' ? /expected 4 to be 3/ : /expected 7 to be 6/);
    }
  } else if (scenario === 'throw') {
    assert.deepEqual(report.errors, []);
    assert.deepEqual(report.tests, [{ name: 'intentional throw', state: 'failed' }]);
    assert.equal(report.reason, 'failed');
    assert.equal(report.testErrors.length, 1);
    assert.equal(report.testErrors[0].message, 'intentional test throw');
    assert.match(report.testErrors[0].stack, /throw.test.ts:2:/);
  } else if (scenario === 'import-throw') {
    assert.deepEqual(report.tests, []);
    assert.equal(report.errors.length, 1);
    assert.equal(report.errors[0].message, 'intentional import throw');
    assert.match(report.errors[0].stack, /import-throw.test.ts:1:7/);
    assert.equal(report.reason, 'failed');
  } else assert.fail(`Unexpected completed deadline report: ${scenario}`);
}

async function readEvents(path) {
  try { return (await readFile(path, 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
}

// A hung test and its descendants share a dedicated controller process group.
// The deadline kills the full group, then verifies every observed live identity is gone.
async function invoke(input, folder) {
  await mkdir(folder);
  const inputFile = join(folder, 'input.json');
  input.result = join(folder, 'result.json'); input.workers = join(folder, 'workers.json');
  await writeFile(inputFile, JSON.stringify(input, null, 2) + '\n');
  const child = spawn(process.execPath, ['--experimental-import-meta-resolve', script, '--controller', inputFile], { detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const stdout = [], stderr = [];
  child.stdout.on('data', chunk => stdout.push(chunk)); child.stderr.on('data', chunk => stderr.push(chunk));
  let deadlineFired = false, observedAtDeadline = [], launchError, killError;
  child.on('error', error => { launchError = String(error); });
  const start = performance.now();
  const timer = setTimeout(async () => {
    deadlineFired = true;
    try {
      observedAtDeadline = await processGroup(child.pid);
      process.kill(-child.pid, 'SIGKILL');
    } catch (error) { killError = String(error); }
  }, input.scenario === 'deadline' ? 5000 : 30000);
  const status = await new Promise(done => child.once('close', (code, signal) => done({ code, signal })));
  clearTimeout(timer);
  const durationMs = performance.now() - start;
  let remaining = await processGroup(child.pid);
  const residualLive = remaining.filter(row => row.state !== 'Z' && row.state !== 'X');
  if (residualLive.length) {
    try { process.kill(-child.pid, 'SIGKILL'); } catch (error) { killError ??= String(error); }
  }
  for (let attempt = 0; attempt < 50 && remaining.length; attempt++) {
    await delay(20); remaining = await processGroup(child.pid);
  }
  const cleanup = { ...status, deadlineFired, durationMs, launchError, killError, observedAtDeadline, residualLive, remaining, noLiveDescendants: remaining.every(row => ['Z','X'].includes(row.state)) };
  await writeFile(join(folder, 'stdout.log'), Buffer.concat(stdout));
  await writeFile(join(folder, 'stderr.log'), Buffer.concat(stderr));
  await writeFile(join(folder, 'status.json'), JSON.stringify(cleanup, null, 2) + '\n');
  assert.ok(!launchError && !killError, JSON.stringify(cleanup));
  assert.equal(residualLive.length, 0, 'Unexpected live descendants after controller completion');
  assert.ok(cleanup.noLiveDescendants, 'Descendant cleanup failed');
  assert.deepEqual(remaining, [], 'Process group identities remain, including unreaped zombies');
  if (input.scenario === 'deadline') {
    assert.ok(deadlineFired && status.signal === 'SIGKILL', 'Missing hard deadline termination');
    assert.ok(durationMs < 10000, 'Hard deadline did not bound termination');
    const events = await readEvents(input.events);
    // Descendant event has no scenario field: identify through this controller's group.
    const actual = events.filter(e => e.type === 'descendant' && observedAtDeadline.some(row => row.pid === e.pid && row.ppid === e.ppid));
    assert.equal(actual.length, 1, 'Deadline fixture must really spawn a live descendant');
    assert.ok(observedAtDeadline.length >= 3, 'Controller, fresh worker and descendant must be observed');
    return { deadline: true, cleanup };
  }
  assert.equal(deadlineFired, false, 'Unexpected timeout');
  // Vitest sets exitCode=1 for correctly observed mutant kills/throws.
  assert.equal(status.code, 1, JSON.stringify(cleanup));
  const result = JSON.parse(await readFile(input.result, 'utf8'));
  const expected = input.scenario === 'sequence' ? jobs : ['inactive'];
  assert.equal(result.reports.length, expected.length);
  assert.equal(result.workers.length, expected.length);
  assert.equal(new Set(result.workers.map(w => w.pid)).size, expected.length, 'Worker reused');
  result.reports.forEach((report, index) => validateReport(report, input.scenario, expected[index]));
  const events = await readEvents(input.events);
  const setup = events.filter(e => e.type === 'setup' && result.workers.some(w => w.pid === e.pid));
  assert.deepEqual(setup.map(e => e.selector), expected, 'Setup/import selector differs from admitted job');
  assert.equal(new Set(setup.map(e => e.realm)).size, expected.length, 'Realm reused');
  if (input.scenario === 'sequence') {
    const own = events.filter(e => result.workers.some(w => w.pid === e.pid));
    assert.equal(own.filter(e => e.type === 'stack').length, 4, 'Missing mapped stack evidence');
    const hooks = own.filter(e => e.type === 'hooks');
    assert.equal(hooks.length, 4);
    hooks.forEach(e => assert.deepEqual(e.counts, { count: 1, before: 5, after: 5, all: 1 }));
    assert.equal(own.filter(e => e.type === 'dirtied-clock' && e.now === 123456789).length, 4);
  }
  assert.equal(events.filter(e => e.type === 'late').length, 0, 'Late work escaped job drain');
  return { ...result, cleanup };
}

function comparable(result) {
  return result.deadline ? { deadline: true } : result.reports.map(r => ({ reason: r.reason, tests: r.tests, errors: r.errors.map(e => e.message), testErrors: r.testErrors.map(e => ({ name: e.name, message: e.message })) }));
}

export async function main(output) {
  await mkdir(output); // Never overwrite any attempt.
  const evidence = { format: 1, status: 'incomplete', priorNegativeCommit: '328917cb2bee82b2059d91fdcb12f7f723fb0f0a', performance: 'Unmeasured: wall, CPU, aggregate RSS, paired ratios/CIs', runs: {} };
  try {
    assert.equal(process.platform, 'linux', 'Linux process identity/cleanup oracle required');
    evidence.node = { version: process.version, platform: process.platform, arch: process.arch, sha256: hash(await readFile(process.execPath)) };
    evidence.versions = Object.fromEntries(await Promise.all(Object.keys(pins).map(async name => [name, JSON.parse(await readFile(require.resolve(name + '/package.json'), 'utf8')).version])));
    checkVersions(evidence.versions);
    evidence.sources = await manifest(import.meta.dirname);
    evidence.lock = { path: join(repo, 'package-lock.json'), sha256: hash(await readFile(join(repo, 'package-lock.json'))) };
    await writeFile(join(output, 'source-manifest.json'), JSON.stringify(evidence.sources, null, 2) + '\n');
    const installation = JSON.stringify(await manifest(join(repo, 'node_modules')), null, 2) + '\n';
    await writeFile(join(output, 'installation.json'), installation);
    evidence.installationSha256 = hash(installation);
    const bundle = await buildCandidate(output, evidence);
    const events = join(output, 'events.jsonl'), root = join(output, 'fixture');
    const fixtures = await makeFixture(root, events);
    evidence.fixtures = Object.fromEntries(Object.entries(fixtures).map(([name, code]) => [name, hash(code)]));
    for (const scenario of scenarios) {
      evidence.runs[scenario] = {};
      for (const mode of ['stock', 'candidate']) {
        evidence.runs[scenario][mode] = await invoke({ root, events, scenario, bundle: mode === 'candidate' ? bundle : undefined }, join(output, scenario + '-' + mode));
      }
      assert.deepEqual(comparable(evidence.runs[scenario].candidate), comparable(evidence.runs[scenario].stock), `Stock/candidate mismatch ${scenario}`);
      await writeFile(join(output, 'evidence.json'), JSON.stringify(evidence, null, 2) + '\n');
    }
    const observed = await readEvents(events);
    for (const scenario of scenarios) {
      const setup = observed.filter(e => e.type === 'setup' && e.scenario === scenario);
      assert.equal(setup.length, scenario === 'sequence' ? 8 : 2);
      assert.equal(new Set(setup.map(e => e.pid)).size, setup.length);
      assert.equal(new Set(setup.map(e => e.realm)).size, setup.length);
      assert.deepEqual(setup.map(e => e.selector), scenario === 'sequence' ? [...jobs,...jobs] : ['inactive','inactive']);
    }
    assert.equal(new Set(observed.filter(e => e.type === 'setup').map(e => e.pid)).size, 14, 'Any job PID reuse');
    assert.equal(observed.filter(e => e.type === 'late').length, 0, 'Late asynchronous work escaped drain');
    assert.equal(observed.filter(e => e.type === 'dirtied-clock').length, 8);
    const hookEvents = observed.filter(e => e.type === 'hooks' && e.counts.before === 5);
    assert.equal(hookEvents.length, 8);
    hookEvents.forEach(e => assert.deepEqual(e.counts, { count: 1, before: 5, after: 5, all: 1 }));
    assert.equal(observed.filter(e => e.type === 'stack').length, 8);
    for (const [name, digest] of Object.entries(evidence.fixtures)) assert.equal(hash(await readFile(join(root, name))), digest, `Fixture changed ${name}`);
    assert.equal(hash(await readFile(evidence.bundle.path)), evidence.bundle.sha256);
    assert.equal(hash(await readFile(evidence.lock.path)), evidence.lock.sha256, 'Lockfile changed');
    evidence.status = 'local structural matrix passed; native qualification pending';
  } catch (error) {
    evidence.status = 'structural failure'; evidence.failure = { message: error.message, stack: error.stack }; process.exitCode = 1;
  } finally {
    await writeFile(join(output, 'evidence.json'), JSON.stringify(evidence, null, 2) + '\n');
  }
  console.log(JSON.stringify({ output, status: evidence.status, failure: evidence.failure?.message }));
}

if (process.argv[1] === script) {
  if (process.argv[2] === '--controller') await controller(JSON.parse(await readFile(process.argv[3], 'utf8')));
  else { assert.equal(process.argv.length, 3, 'Usage: node --experimental-import-meta-resolve probe.mjs NEW_OUTPUT_DIR'); await main(resolve(process.argv[2])); }
}
