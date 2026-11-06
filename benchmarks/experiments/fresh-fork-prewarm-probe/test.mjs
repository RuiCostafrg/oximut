import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { jobs, parseArguments, validateControllerStatus, validateObservations } from './probe.mjs';

test('a selector control must fail for the intended selector, with complete normal cleanup', () => {
  const status = { status: { code: 1, signal: null }, timedOut: false,
    cleanupRequired: false, cleanupVerified: true };
  const result = { closed: true, disposed: true, cleanupErrors: [], failureStage: 'selector-oracle',
    observedSelector: null, expectedSelector: jobs[1].activeMutant };
  validateControllerStatus(status, result, true);
  for (const change of [
    s => { s.status.code = 0; }, s => { s.status.code = 23; },
    s => { s.status.code = null; s.status.signal = 'SIGTERM'; },
    s => { s.timedOut = true; }, s => { s.cleanupRequired = true; },
    s => { s.cleanupVerified = false; },
    s => { s.interrupted = true; }, s => { s.spawnError = 'injected'; },
  ]) {
    const altered = structuredClone(status); change(altered);
    assert.throws(() => validateControllerStatus(altered, result, true));
  }
  for (const change of [
    r => { r.failureStage = 'unrelated-import'; }, r => { r.closed = false; },
    r => { r.disposed = false; }, r => { r.cleanupErrors = ['stop failed']; },
    r => { r.observedSelector = 'a'.repeat(64); }, r => { r.expectedSelector = null; },
  ]) {
    const altered = structuredClone(result); change(altered);
    assert.throws(() => validateControllerStatus(status, altered, true));
  }
});

test('successful fixture evidence cannot excuse forced cleanup or incomplete close', () => {
  const status = { status: { code: 0, signal: null }, timedOut: false,
    cleanupRequired: false, cleanupVerified: true };
  const result = { closed: true, disposed: true, cleanupErrors: [], freshnessAndHandoff: true };
  validateControllerStatus(status, result);
  assert.throws(() => validateControllerStatus({ ...status, cleanupRequired: true }, result));
  assert.throws(() => validateControllerStatus(status, { ...result, closed: false }));
  assert.throws(() => validateControllerStatus(status, { ...result, failure: 'hidden error' }));
});

test('join every selector/setup/module/state record to its exact independent job and worker', () => {
  const workers = jobs.map((job, i) => ({ pid: 100 + i, startTime: String(12345 + i), requestedId: job.label }));
  const observed = jobs.flatMap((job, i) => ['setup-file', 'module', 'state'].map(phase => ({
    phase, requestedId: job.label, activeMutant: job.activeMutant, pid: workers[i].pid,
    realm: '12345678-1234-4321-1234-' + String(i).padStart(12, '0'),
    ...(phase === 'state' ? { local: 0, cjs: 0, esm: 0, global: null,
      env: null, builtin: null, spy: 'original', fakeClock: false } : {}),
  })));
  validateObservations(observed, workers);
  const changes = [
    r => { r.pop(); }, r => { r[0].activeMutant = ''; },
    r => { r[3].activeMutant = null; }, r => { r[4].requestedId = 'baseline'; },
    r => { r[0].phase = 'module'; }, r => { r[0].pid = 999; },
    r => { r[0].realm = 'bad realm'; },
    r => { for (const row of r.slice(3, 6)) row.realm = r[0].realm; },
  ];
  for (const field of ['local', 'cjs', 'esm', 'global', 'env', 'builtin', 'spy', 'fakeClock']) {
    changes.push(r => { delete r[2][field]; });
    changes.push(r => { r[2][field] = 'dirty'; });
  }
  for (const change of changes) {
    const altered = structuredClone(observed); change(altered);
    assert.throws(() => validateObservations(altered, workers));
  }
  assert.throws(() => validateObservations(observed, workers.slice(1)));
});

test('parse fresh-output and case options independently, rejecting missing and repeated values', () => {
  assert.deepEqual(parseArguments([]), { mode: 'matrix' });
  assert.deepEqual(parseArguments(['--output', 'a b', '--case', 'stock']), { output: 'a b', mode: 'stock' });
  assert.deepEqual(parseArguments(['--case', 'prewarm', '--output', 'a']), { output: 'a', mode: 'prewarm' });
  for (const args of [['--output'], ['--output', ''], ['--output', '--case'],
    ['--case', 'nope'], ['--wat', 'value'], ['--case', 'stock', '--case', 'prewarm'],
    ['--output', 'one', '--output', 'two']]) assert.throws(() => parseArguments(args));
});

test('real fork transport retains early exits and readiness deadlines and removes message callbacks',
  { timeout: 15000 }, async () => {
    const { createForkPool } = await import('./pool.mjs');
    const require = createRequire(import.meta.url);
    const distPath = join(dirname(require.resolve('vitest/package.json')), 'dist');
    const entry = join(distPath, 'workers/forks.js');
    const root = await mkdtemp(join(tmpdir(), 'prewarm-failure-contract-'));
    try {
      for (const crash of [true, false]) {
        const workerEntry = join(root, crash ? 'crash.mjs' : 'missing-ready.mjs');
        await writeFile(workerEntry, crash ? 'process.exit(23);\n' :
          'process.on("message",m=>{if(m.probeEcho)process.send({probeEcho:true});});\n' +
          'await import(' + JSON.stringify(pathToFileURL(entry).href) + ');\n');
        const pool = createForkPool({
          launcher: fileURLToPath(new URL('./launcher.mjs', import.meta.url)),
          observationFile: join(root, 'never-imported.jsonl'), workerEntry, readinessMs: 1500,
        });
        pool.setNext(jobs[0]);
        // Only failure-transport tests use a minimal logger; positive fixtures use createVitest.
        const worker = pool.pool.createPoolWorker({ distPath, execArgv: [], env: { ...process.env },
          project: { vitest: { logger: { outputStream: process.stdout, errorStream: process.stderr } } } });
        const started = worker.start();
        // Register rejection immediately, including while testing callback removal.
        const rejected = assert.rejects(started, crash ? /exited before normal stop/ : /readiness deadline/);
        try {
          if (!crash) {
            let calls = 0;
            const callback = () => calls++;
            worker.on('message', callback); worker.off('message', callback);
            const echo = new Promise(resolve => worker.on('message', message => {
              if (message.probeEcho) resolve();
            }));
            worker.send({ probeEcho: true });
            await echo;
            assert.equal(calls, 0, 'off must remove the exact wrapped callback');
          }
          await rejected;
        } finally { await pool.dispose(); }
        assert.ok(pool.failures.length > 0);
        assert.throws(() => pool.assertDrained(), 'failed worker cannot produce passing evidence');
        const pid = pool.evidence[0].pid;
        assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
      }
    } finally { await rm(root, { recursive: true, force: true }); }
  });
