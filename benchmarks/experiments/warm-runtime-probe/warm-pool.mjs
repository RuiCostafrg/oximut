// Experiment only: a warm host is NOT yet a fresh-process equivalent.
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { VmThreadsPoolWorker } from 'vitest/node';

export function createWarmPool() {
  let worker;
  class WarmWorker extends VmThreadsPoolWorker {
    name = 'oximut-warm-vm-probe';
    callbacks = new Map();
    piped = false;
    constructor(options) {
      super(options);
      this.entrypoint = fileURLToPath(new URL('./warm-vm-worker.mjs', import.meta.url));
    }
    on(event, callback) {
      super.on(event, callback);
      this.callbacks.set(callback, event);
    }
    off(event, callback) {
      super.off(event, callback);
      this.callbacks.delete(callback);
    }
    async start() {
      assert.equal(this.piped, false, 'warm starts must follow a completed stop handshake');
      await super.start();
      this.piped = true;
    }
    async stop() {
      // PoolRunner has awaited 'stopped' and closed its RPC before this call.
      // Release only this runner's listeners/streams; keep the host thread.
      for (const [callback,event] of this.callbacks) super.off(event, callback);
      this.callbacks.clear();
      if (this.piped) {
        this.thread.stdout.unpipe(this.stdout);
        this.stdout.setMaxListeners(this.stdout.getMaxListeners() - 1);
        this.thread.stderr.unpipe(this.stderr);
        this.stderr.setMaxListeners(this.stderr.getMaxListeners() - 1);
        this.piped = false;
      }
    }
    async dispose() {
      if (this._thread) {
        await this.stop();
        await this.thread.terminate();
        this._thread = undefined;
      }
    }
  }
  return {
    pool: { name: 'oximut-warm-vm-probe', createPoolWorker(options) {
      assert.equal(options.project.config.maxWorkers, 1);
      worker ??= new WarmWorker(options);
      return worker;
    } },
    async dispose() { await worker?.dispose(); },
  };
}
