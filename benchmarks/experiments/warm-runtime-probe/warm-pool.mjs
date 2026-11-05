// Experiment only: a warm host is NOT yet a fresh-process equivalent.
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { VmThreadsPoolWorker } from 'vitest/node';

export function createWarmPool({ guarded = false } = {}) {
  let worker;
  class WarmWorker extends VmThreadsPoolWorker {
    name = 'oximut-warm-vm-probe';
    callbacks = new Map();
    piped = false;
    guard = undefined;
    lifecycle = [];
    constructor(options) {
      super(options);
      this.entrypoint = fileURLToPath(new URL('./warm-vm-worker.mjs', import.meta.url));
    }
    on(event, callback) {
      const wrapped = event === 'message' ? message => {
        if (message?.type === 'stopped') {
          this.guard = message.__oximutWarmGuard;
          this.lifecycle.push({ pid: process.pid, threadId: this.thread.threadId,
            available: this.guard?.available === true, dirty: this.guard?.dirty !== false,
            envChanges: this.guard?.envChanges ?? null, fsChanges: this.guard?.fsChanges ?? null });
        }
        callback(message);
      } : callback;
      super.on(event, wrapped);
      this.callbacks.set(wrapped, { event, original: callback });
    }
    off(event, callback) {
      const entry = [...this.callbacks].find(([, item]) => item.event === event && item.original === callback);
      const wrapped = entry?.[0] ?? callback;
      super.off(event, wrapped);
      this.callbacks.delete(wrapped);
    }
    async start() {
      assert.equal(this.piped, false, 'warm starts must follow a completed stop handshake');
      await super.start();
      this.piped = true;
    }
    async stop() {
      // PoolRunner has awaited 'stopped' and closed its RPC before this call.
      // Release only this runner's listeners/streams; keep the host thread.
      for (const [callback,item] of this.callbacks) super.off(item.event, callback);
      this.callbacks.clear();
      if (this.piped) {
        this.thread.stdout.unpipe(this.stdout);
        this.stdout.setMaxListeners(this.stdout.getMaxListeners() - 1);
        this.thread.stderr.unpipe(this.stderr);
        this.stderr.setMaxListeners(this.stderr.getMaxListeners() - 1);
        this.piped = false;
      }
      if (!guarded || (this.guard?.available === true && this.guard.dirty === false)) this.guard = undefined;
      else {
        if (this._thread) await this.thread.terminate();
        this._thread = undefined;
        this.guard = undefined;
      }
    }
    async dispose() {
      if (this._thread) {
        await this.stop();
        if (this._thread) {
          await this.thread.terminate();
          this._thread = undefined;
        }
      }
    }
  }
  return {
    pool: { name: 'oximut-warm-vm-probe', createPoolWorker(options) {
      assert.equal(options.project.config.maxWorkers, 1);
      worker ??= new WarmWorker(options);
      return worker;
    } },
    get evidence() { return worker?.lifecycle ?? []; },
    async dispose() { await worker?.dispose(); },
  };
}
