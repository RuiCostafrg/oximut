import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ForksPoolWorker } from 'vitest/node';

/** Construct a new pinned Vitest fork worker for each task, never a warm host. */
export function createForkPool(entrypoint) {
  let sequence = 0;
  const workers = [];
  return {
    workers,
    pool: {
      name: 'fresh-fork-bundle-probe',
      /** Keep the framework's fork transport and change only the candidate entry. */
      createPoolWorker(options) {
        const worker = new class extends ForksPoolWorker {
          evidence = {};
          /** Capture the newly launched child's identity before test dispatch. */
          async start() {
            assert.ok(this.execArgv.includes('--experimental-import-meta-resolve'),
              'source-relative resolution requires the pinned Vitest launch flag');
            await super.start();
            const child = this._fork;
            const fields = readFileSync(`/proc/${child.pid}/stat`, 'utf8').split(') ').pop().split(' ');
            assert.equal(Number(fields[1]), process.pid, 'worker must be a direct child');
            assert.match(fields[19], /^\d+$/);
            this.evidence = { pid: child.pid, ppid: process.pid, startTime: fields[19],
              entrypoint: this.entrypoint, execArgv: [...this.execArgv] };
          }
          /** Await the original worker teardown and retain its actual exit. */
          async stop() {
            const child = this._fork;
            await super.stop();
            this.evidence.exitCode = child.exitCode;
            this.evidence.signal = child.signalCode;
          }
        }(options);
        if (entrypoint) worker.entrypoint = entrypoint;
        const id = ++sequence;
        workers.push({ id, worker });
        return worker;
      },
    },
    /** Accept only the observed normal isolated-fork shutdown outcomes. */
    assertDrained() {
      assert.equal(workers.length, sequence);
      for (const { worker } of workers) {
        assert.ok(worker.evidence.pid > 0);
        assert.ok(worker.evidence.exitCode === 0 || (worker.evidence.exitCode === null && worker.evidence.signal === 'SIGTERM'));
      }
    },
  };
}
