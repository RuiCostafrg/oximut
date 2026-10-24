import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { setTimeout as delay } from 'node:timers/promises';
import { openController, comparable } from './index.mjs';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '../..');

async function withController(options, action) {
  const controller = await openController(options);
  try { return await action(controller); } finally { await controller.close(); }
}

async function fixtureExperiment(name) {
  const options = { root: resolve(repo, 'fixtures', name), coverage: true };
  const reused = await withController(options, async (controller) => {
    const discovery = await controller.discover();
    const runs = [];
    for (let index = 0; index < 3; index++) runs.push(await controller.run());
    await assert.rejects(controller.run({ testIds: [] }), /nonempty/);
    await assert.rejects(controller.run({ testIds: ['unknown-test-id'] }), /Unknown test ID/);
    const selection = await controller.run({ testIds: [discovery.tests[0].id] });
    const selectionMatchesBaseline = isDeepStrictEqual(
      comparable({ ...selection, tests: selection.tests.filter((test) => test.state !== 'skipped') }),
      comparable({ ...runs[0], tests: runs[0].tests.filter((test) => test.id === discovery.tests[0].id) }),
    );
    return { discovery, runs, selection, selectionMatchesBaseline, controllerPid: controller.pid, versions: controller.versions };
  });
  const fresh = [];
  for (let index = 0; index < 2; index++) {
    fresh.push(await withController(options, async (controller) => {
      const discovery = await controller.discover();
      return { discovery, run: await controller.run() };
    }));
  }
  const reference = comparable(fresh[0].run);
  const equivalence = [...reused.runs, ...fresh.map(({ run }) => run)].every((run) => isDeepStrictEqual(reference, comparable(run)));
  const stableDiscovery = [reused.discovery, ...fresh.map(({ discovery }) => discovery)].every((discovery) => isDeepStrictEqual(discovery.tests.map(({ id }) => id).sort(), fresh[0].discovery.tests.map(({ id }) => id).sort()));
  const completeCoverage = reused.runs.every((run) => run.coverage.complete && run.coverage.files.length > 0);
  return { name, reused, fresh, equivalence, stableDiscovery, completeCoverage };
}

async function makeProbe() {
  const root = await mkdtemp(resolve(repo, 'packages/vitest-adapter/.experiment-'));
  await mkdir(resolve(root, 'src'));
  await mkdir(resolve(root, 'test'));
  await writeFile(resolve(root, 'src/state.mjs'), `export const phase = process.env.OXIMUT_PHASE;\nexport let count = 0;\nexport function advance() { return ++count; }\n`);
  await writeFile(resolve(root, 'test/state.test.mjs'), `
import { test, expect, beforeEach, afterEach, vi } from 'vitest';
import { phase, count, advance } from '../src/state.mjs';
const evaluation = crypto.randomUUID();
const mock = vi.fn();
beforeEach(() => mock.mockReset());
afterEach(() => expect(mock).toHaveBeenCalledTimes(1));
test('clean module and mock state', async ({ task }) => {
  task.meta.observation = { pid: process.pid, phase, evaluation, count };
  expect(count).toBe(0);
  expect(advance()).toBe(1);
  await Promise.resolve();
  mock();
});
test('duplicate name', ({ task }) => { task.meta.observation = { pid: process.pid, phase, evaluation }; mock(); });
test('duplicate name', ({ task }) => { task.meta.observation = { pid: process.pid, phase, evaluation }; mock(); });
test.each([1, 2])('parameter %i', () => { mock(); });
`);
  return root;
}

async function reuseProbe(root) {
  return withController({ root, config: false, environment: { OXIMUT_PHASE: 'alpha' } }, async (controller) => {
    const discovery = await controller.discover();
    const runs = [await controller.run(), await controller.run(), await controller.run({ environment: { OXIMUT_PHASE: 'beta' } })];
    const observations = runs.map((run) => run.tests.find((test) => test.name === 'clean module and mock state').meta.observation);
    const duplicates = discovery.tests.filter((test) => test.name === 'duplicate name');
    const selection = await controller.run({ testIds: [duplicates[1].id] });
    const selected = selection.tests.filter((test) => test.state !== 'skipped');
    const exactDuplicateSelection = selection.outcome === 'passed' && selected.length === 1 && selected[0].id === duplicates[1].id;
    const sameController = runs.every((run) => run.controllerPid === controller.pid);
    const moduleReevaluated = new Set(observations.map(({ evaluation }) => evaluation)).size === observations.length;
    const environmentUpdated = observations[2].phase === 'beta';
    const freshBeta = await withController({ root, config: false, environment: { OXIMUT_PHASE: 'beta' } }, async (fresh) => {
      await fresh.discover();
      return fresh.run();
    });
    const freshBetaPhase = freshBeta.tests.find((test) => test.name === 'clean module and mock state').meta.observation.phase;
    return { discovery, runs, observations, selection, exactDuplicateSelection, sameController, moduleReevaluated, environmentUpdated, freshBetaPhase, freshBeta, distinctWorkerPids: [...new Set(observations.map(({ pid }) => pid))], controllerPid: controller.pid };
  });
}

async function cancellationProbe(root) {
  const hangRoot = resolve(root, 'hang');
  await mkdir(resolve(hangRoot, 'test'), { recursive: true });
  const marker = resolve(hangRoot, 'started.json');
  await writeFile(resolve(hangRoot, 'test/hang.test.mjs'), `
import { test } from 'vitest';
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
test('CPU-hung worker with detached descendant', () => {
  const descendant = spawn(process.execPath, ['-e', 'process.on("SIGTERM",()=>{});setInterval(()=>{},1000)'], { detached: true, stdio: 'ignore' });
  writeFileSync(${JSON.stringify(marker)}, JSON.stringify({ workerPid: process.pid, descendantPid: descendant.pid }));
  while (true) {}
}, 60000);
`);
  return withController({ root: hangRoot, config: false }, async (controller) => {
    await controller.discover();
    const cancellation = new AbortController();
    const runPending = controller.run({ deadlineMs: 30000, signal: cancellation.signal });
    // Observe early controller errors while the readiness loop is still polling.
    let runError;
    runPending.catch((error) => { runError = error; });
    // Observe the test, rather than guessing how long worker startup takes.
    // Startup and cleanup have independent bounds; neither can wait forever.
    const readinessStarted = performance.now();
    let started;
    try {
      while (!started) {
        if (runError) throw runError;
        try { started = JSON.parse(await readFile(marker, 'utf8')); }
        catch (error) {
          if (error.code !== 'ENOENT') throw error;
          if (performance.now() - readinessStarted > 20000) throw new Error('Hung test did not signal readiness within 20s');
          await delay(10);
        }
      }
    } finally { cancellation.abort(); }
    const run = await runPending;
    const tracked = run.termination?.tracked ?? [];
    const proved = run.outcome === 'cancelled' && run.termination.allExited && run.termination.allReaped && run.termination.elapsedMs < 3500 && tracked.includes(started.workerPid) && tracked.includes(started.descendantPid);
    return { ...run, started, proved, deadlineMs: 30000, readinessTimeoutMs: 20000, readinessElapsedMs: performance.now() - readinessStarted - run.termination.elapsedMs, platform: process.platform, verification: 'Readiness marker before AbortSignal cancellation; Linux subreaper + waitpid + PID/start-time checks prove exit and reaping' };
  });
}

async function deadlineProbe(root) {
  return withController({ root, config: false }, async (controller) => {
    await controller.discover();
    // This deliberately expires during startup. The readiness probe separately
    // proves cancellation of a test callback that has actually begun hanging.
    const run = await controller.run({ deadlineMs: 1 });
    return { ...run, deadlineMs: 1, proved: run.outcome === 'timeout' && run.termination.allReaped && run.termination.elapsedMs < 3500 };
  });
}

async function normalCleanupProbe(root) {
  const cleanRoot = resolve(root, 'normal-cleanup');
  await mkdir(resolve(cleanRoot, 'test'), { recursive: true });
  const marker = resolve(cleanRoot, 'started.json');
  await writeFile(resolve(cleanRoot, 'test/detached.test.mjs'), `
import { test } from 'vitest';
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
test('passing test leaves a detached child', () => {
  const child = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { detached: true, stdio: 'ignore' });
  child.unref();
  writeFileSync(${JSON.stringify(marker)}, JSON.stringify({ descendantPid: child.pid }));
});
`);
  const controller = await openController({ root: cleanRoot, config: false });
  let run;
  let cleanup;
  try { await controller.discover(); run = await controller.run(); }
  finally { cleanup = await controller.close(); }
  const started = JSON.parse(await readFile(marker, 'utf8'));
  return { run, cleanup, started, proved: run.outcome === 'passed' && cleanup.allReaped && cleanup.tracked.includes(started.descendantPid) };
}

export async function runExperiment() {
  const started = new Date().toISOString();
  const fixtures = [];
  for (const name of ['pure', 'stateful']) fixtures.push(await fixtureExperiment(name));
  const root = await makeProbe();
  let reuse;
  let cancellation;
  let deadline;
  let normalCleanup;
  try { reuse = await reuseProbe(root); cancellation = await cancellationProbe(root); deadline = await deadlineProbe(root); normalCleanup = await normalCleanupProbe(root); }
  finally { await rm(root, { recursive: true, force: true }); }
  const gates = {
    repeatedFreshAndReusedResultsAgree: fixtures.every((fixture) => fixture.equivalence),
    fixturesBaselinePassed: fixtures.every((fixture) => [...fixture.reused.runs, fixture.reused.selection, ...fixture.fresh.map(({ run }) => run)].every((run) => run.outcome === 'passed')),
    stableIdentities: fixtures.every((fixture) => fixture.stableDiscovery),
    selectedResultsAgree: fixtures.every((fixture) => fixture.reused.selectionMatchesBaseline) && reuse.exactDuplicateSelection,
    coverageCollected: fixtures.every((fixture) => fixture.completeCoverage),
    isolatedModuleReevaluation: reuse.moduleReevaluated,
    cpuHangTerminated: cancellation.proved,
    absoluteDeadlineEnforced: deadline.proved,
    cleanProbeRuns: reuse.runs.every((run) => run.outcome === 'passed'),
    reusedEnvironmentUpdated: reuse.environmentUpdated,
    freshEnvironmentReferencePassed: reuse.freshBeta.outcome === 'passed' && reuse.freshBetaPhase === 'beta',
    normalDetachedChildReaped: normalCleanup.proved,
  };
  return { schemaVersion: 1, experiment: 'M0-vitest-lifecycle', started, completed: new Date().toISOString(), runtime: { node: process.version, platform: process.platform, arch: process.arch }, fixtures, reuse, cancellation, deadline, normalCleanup, gates, passed: Object.values(gates).every(Boolean) };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const report = await runExperiment();
    const outputIndex = process.argv.indexOf('--output');
    if (outputIndex !== -1) {
      if (!process.argv[outputIndex + 1]) throw new Error('--output requires a path');
      const output = resolve(process.argv[outputIndex + 1]);
      await mkdir(dirname(output), { recursive: true });
      await writeFile(output, `${JSON.stringify(report, null, 2)}\n`);
      console.log(JSON.stringify({ output, passed: report.passed, gates: report.gates }));
    } else console.log(JSON.stringify(report));
    if (!report.passed) process.exitCode = 1;
  } catch (error) { console.error(error.stack ?? error); process.exitCode = 1; }
}
