import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { ForksPoolWorker } from 'vitest/node';

function identity(pid) {
  const value = readFileSync('/proc/' + pid + '/stat', 'utf8');
  const fields = value.slice(value.lastIndexOf(')') + 1).trim().split(/\s+/);
  assert.equal(Number(value.slice(0, value.indexOf('(')).trim()), pid);
  assert.match(fields[19], /^\d+$/);
  assert.equal(Number(fields[1]), process.pid);
  return { pid, ppid: Number(fields[1]), startTime: fields[19] };
}

function observations(file) {
  try { return readFileSync(file, 'utf8').split('\n').filter(Boolean).map(JSON.parse); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
}

function compatible(options) {
  const env = { ...options.env };
  delete env.OXIMUT_ACTIVE_MUTANT; delete env.OXIMUT_PROBE_JOB_ID;
  return JSON.stringify({ distPath: options.distPath, execArgv: options.execArgv,
    env: Object.fromEntries(Object.entries(env).sort(([a], [b]) => a.localeCompare(b))) });
}

/** Keep stock PoolRunner handshakes; each returned worker executes only one job. */
export function createForkPool({ launcher, observationFile, workerEntry, readinessMs = 10_000 }) {
  const candidate = Boolean(launcher);
  const workers = [], allWorkers = [], failures = [];
  let assignment, pending;

  function fail(worker, error) {
    const message = String(error?.stack || error);
    worker.evidence.failure ??= message;
    failures.push(message);
    worker.rejectReady?.(error);
  }

  function noProjectImports(worker) {
    assert.equal(observations(observationFile).filter(row => row.pid === worker.evidence.pid).length, 0,
      'unused worker imported project code before assignment');
    worker.evidence.noImportsBeforeAssignment = true;
  }

  function makeWorker(options, spare) {
    const worker = new class extends ForksPoolWorker {
      evidence = { preparedAsSpare: spare };
      messageWrappers = new Map();
      nonce = randomUUID();
      ready = new Promise((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
      /** Preserve on/off callback identity while keeping readiness out of stock RPC. */
      on(event, callback) {
        if (!candidate || event !== 'message') return super.on(event, callback);
        const wrapped = message => { if (message?.oximutFrameworkImported !== true) callback(message); };
        const entries = this.messageWrappers.get(callback) ?? [];
        entries.push(wrapped); this.messageWrappers.set(callback, entries);
        return super.on(event, wrapped);
      }
      off(event, callback) {
        if (!candidate || event !== 'message') return super.off(event, callback);
        const entries = this.messageWrappers.get(callback);
        const wrapped = entries?.pop();
        if (!entries?.length) this.messageWrappers.delete(callback);
        return super.off(event, wrapped ?? callback);
      }
      /** Repeated start joins the one existing launch, including its readiness/error. */
      start() {
        if (this.launch) return this.launch;
        this.launch = (async () => {
          this.evidence.spawnStartedAt = performance.now();
          const started = super.start(), child = this._fork;
          child.on('error', error => fail(this, error));
          child.once('exit', (code, signal) => {
            Object.assign(this.evidence, { exitCode: code, signal, exitedAt: performance.now() });
            if (!this.stopping) fail(this, new Error('worker exited before normal stop'));
          });
          child.on('message', message => {
            if (message?.oximutFrameworkImported === true) {
              if (!candidate || message.pid !== child.pid || message.nonce !== this.nonce ||
                  this.evidence.readyReceivedAt != null ||
                  !Number.isFinite(message.importDurationMs) || message.importDurationMs < 0) {
                fail(this, new Error('invalid or duplicate framework-import readiness'));
                return;
              }
              this.evidence.readyReceivedAt = performance.now();
              this.evidence.childImportDurationMs = message.importDurationMs;
              this.resolveReady();
            }
            if (message?.__vitest_worker_response__ === true) {
              if (message.type === 'started') this.evidence.startedResponseAt = performance.now();
              if (message.type === 'testfileFinished') this.evidence.runResponseAt = performance.now();
            }
          });
          Object.assign(this.evidence, identity(child.pid), { entrypoint: this.entrypoint });
          await started;
          if (candidate) {
            let timer;
            try {
              await Promise.race([this.ready, new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error('framework readiness deadline')), readinessMs);
              })]);
            } finally { clearTimeout(timer); }
          }
          if (this.evidence.failure || child.exitCode !== null || child.signalCode !== null)
            throw new Error(this.evidence.failure || 'worker already exited');
        })();
        this.launch.catch(error => fail(this, error));
        return this.launch;
      }
      /** Supply the selector only on the real start message, before setup imports. */
      send(message) {
        let outgoing = message;
        if (message?.__vitest_worker_request__ === true && message.type === 'start') {
          assert.ok(this.job, 'worker has no independently assigned job');
          assert.equal(this.evidence.startSentAt, undefined, 'worker started twice');
          if (candidate) noProjectImports(this);
          this.evidence.startSentAt = performance.now();
          if (candidate) outgoing = { ...message, oximutAssignment: {
            ...this.job, activeMutant: this.job.corrupt ? null : this.job.activeMutant, nonce: this.nonce,
          } };
        }
        if (message?.__vitest_worker_request__ === true && message.type === 'run') {
          assert.equal(this.evidence.runSentAt, undefined, 'worker executed twice');
          this.evidence.runSentAt = performance.now();
          const result = super.send(outgoing);
          if (candidate && !pending) {
            pending = makeWorker(options, true);
            void pending.start();
          }
          return result;
        }
        return super.send(outgoing);
      }
      /** Normal termination uses the unchanged stock worker stop and kill timer. */
      async stop() {
        const child = this._fork;
        if (!child || this.stopped) return;
        this.stopping = true;
        if (child.exitCode === null && child.signalCode === null) await super.stop();
        this.stopped = true;
        Object.assign(this.evidence, { exitCode: child.exitCode, signal: child.signalCode,
          stopCompletedAt: performance.now() });
      }
    }(options);
    worker.ready.catch(() => {}); // Errors are retained and fail the controller gate.
    worker.options = options;
    worker.compatibility = compatible(options);
    if (candidate) {
      worker.entrypoint = workerEntry ?? launcher;
      worker.env = { ...options.env, OXIMUT_REAL_FORK_ENTRY: options.distPath + '/workers/forks.js',
        OXIMUT_PREWARM_NONCE: worker.nonce };
      delete worker.env.OXIMUT_ACTIVE_MUTANT;
      delete worker.env.OXIMUT_PROBE_JOB_ID;
    }
    allWorkers.push(worker);
    return worker;
  }

  return {
    workers, failures,
    pool: {
      name: 'fresh-fork-prewarm-probe',
      createPoolWorker(options) {
        assert.ok(assignment, 'controller did not assign a job');
        const worker = pending ?? makeWorker(options, false);
        pending = undefined;
        assert.equal(worker.compatibility, compatible(options), 'prepared worker configuration changed');
        assert.equal(worker.options.project, options.project);
        assert.equal(worker.job, undefined, 'executed worker cannot be reused');
        worker.job = { ...assignment };
        Object.assign(worker.evidence, { requestedId: assignment.label, assignedAt: performance.now() });
        if (worker.evidence.pid) noProjectImports(worker);
        workers.push(worker);
        return worker;
      },
    },
    setNext(job) { assignment = { ...job }; },
    async dispose() {
      const errors = [];
      for (const worker of allWorkers) {
        if (!worker.job) {
          try { await worker.start(); } catch { /* Still stop failed unused children. */ }
          try { if (worker.evidence.pid) noProjectImports(worker); }
          catch (error) { errors.push(error); }
          worker.evidence.disposedUnassigned = true;
        }
        try { await worker.stop(); } catch (error) { errors.push(error); }
      }
      pending = undefined;
      if (errors.length) throw new AggregateError(errors, 'failed to dispose all workers');
    },
    assertDrained() {
      assert.deepEqual(failures, [], failures.join('\n'));
      for (const worker of allWorkers) {
        assert.ok(worker.stopped, 'worker not stopped');
        assert.ok(worker.evidence.exitCode === 0 ||
          (worker.evidence.exitCode === null && worker.evidence.signal === 'SIGTERM'),
        'worker exited abnormally');
      }
    },
    get evidence() { return allWorkers.map(worker => ({ ...worker.evidence })); },
  };
}
