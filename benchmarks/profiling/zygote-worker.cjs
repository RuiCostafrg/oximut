// Replay-only zygote preload for a restored Vitest fork worker. Load it with
// NODE_OPTIONS=--require=<this file> together with OXIMUT_ZYGOTE_ADDON (the
// built benchmarks/experiments/zygote-fork/fork.c), in a worker started with
// --single-threaded. Once Vitest has registered its worker listener, the
// process stops being a worker and forks one fresh child per spawn request.
// Each child receives its own IPC channel and then runs the unmodified Vitest
// worker from the restored state. See benchmarks/experiments/zygote-fork/README.md.
const addonPath = process.env.OXIMUT_ZYGOTE_ADDON;
if (addonPath) {
  const net = require('node:net');
  const { _forkChild } = require('node:child_process');
  const { signals } = require('node:os').constants;
  const addon = require(addonPath);
  delete process.env.OXIMUT_ZYGOTE_ADDON;
  const signalNames = Object.fromEntries(Object.entries(signals).map(([name, number]) => [number, name]));
  const children = new Map();
  const traceDir = process.env.OXIMUT_ZYGOTE_TRACE_DIR;
  const fs = require('node:fs');
  const trace = traceDir
    ? (...fields) => fs.appendFileSync(require('node:path').join(traceDir, `zygote-${addon.getpid()}.log`),
      `${performance.now().toFixed(1)} ${fields.join(' ')}\n`)
    : () => {};
  const queue = [];
  let inflight = false;

  const reap = () => {
    for (let result; (result = addon.waitpidNohang());) {
      const id = children.get(result.pid);
      trace('reaped', id, result.pid, result.code, result.signal);
      children.delete(result.pid);
      if (id !== undefined) {
        process.send({ __oximut_zygote__: 'exit', id, pid: result.pid, code: result.code,
          signal: result.signal === null ? null : signalNames[result.signal] ?? result.signal });
      }
    }
  };

  const becomeChild = (parentEnd, childEnd, env) => {
    process.removeListener('message', onZygoteMessage);
    process.removeListener('internalMessage', onInternalMessage);
    process.removeAllListeners('disconnect');
    process.removeAllListeners('SIGCHLD');
    clearInterval(reaper);
    // Node cached the zygote's pid at bootstrap.
    Object.defineProperty(process, 'pid', { value: addon.getpid(), configurable: true, enumerable: true });
    // Node's exit path disposes the platform and joins worker threads that do
    // not exist after fork(), which hangs. Leave with _exit() once JavaScript
    // 'exit' handlers have run.
    process.reallyExit = (code) => addon._exitNow(code | 0);
    // A requested environment replaces the zygote's, as fork({env}) would.
    if (env) {
      for (const key of Object.keys(process.env)) if (!Object.hasOwn(env, key)) delete process.env[key];
      for (const [key, value] of Object.entries(env)) process.env[key] = String(value);
    }
    addon.closeFd(parentEnd);
    process.once('disconnect', () => {
      addon.clearCloexec(childEnd);
      // _forkChild replaces process.send; keep any wrapper installed before
      // the fork. Node's send() writes through this._send, the new channel.
      const send = process.send;
      _forkChild(childEnd, 'advanced');
      process.send = send;
      trace('child-channel', addon.getpid());
      // Vitest's message listener predates this channel, so hold a reference.
      process.channel.ref();
      process.on('exit', (code) => addon._exitNow(code | 0));
    });
    process.disconnect();
  };

  let busyRetries = 0;
  const spawnNext = () => {
    if (inflight || queue.length === 0) return;
    inflight = true;
    // fork() returns -2 while a V8 worker thread is busy (see fork.c).
    const [parentEnd, childEnd] = addon.socketpair();
    const pid = addon.fork();
    if (pid === -2) {
      addon.closeFd(parentEnd);
      addon.closeFd(childEnd);
      inflight = false;
      busyRetries++;
      setTimeout(spawnNext, 1);
      return;
    }
    const { id, env } = queue.shift();
    if (pid === 0) return becomeChild(parentEnd, childEnd, env);
    const condvar = addon.lastCondvar();
    trace('forked', id, pid, 'queue', queue.length, 'condvar', condvar.condvar.toString(16),
      'workers', condvar.workers, 'offset', condvar.futexOffset, 'busyRetries', busyRetries);
    addon.closeFd(childEnd);
    children.set(pid, id);
    // Never read here: the child's first messages belong to the controller.
    const socket = new net.Socket({ fd: parentEnd, readable: true, writable: true, manualStart: true });
    process.send({ __oximut_zygote__: 'spawned', id, pid }, socket);
  };

  function onInternalMessage(message) {
    if (message?.cmd === 'NODE_HANDLE_ACK' || message?.cmd === 'NODE_HANDLE_NACK') {
      trace('ack', message.cmd);
      inflight = false;
      setImmediate(spawnNext);
    }
  }

  function onZygoteMessage(message) {
    if (message?.__oximut_zygote__ === 'spawn') {
      trace('spawn', message.id, 'inflight', inflight, 'queue', queue.length);
      queue.push(message);
      spawnNext();
    } else if (message?.__oximut_zygote__ === 'shutdown') {
      process.exit(0);
    }
  }

  process.on('message', onZygoteMessage);
  process.on('internalMessage', onInternalMessage);
  // SIGCHLD reaps promptly; the interval is a fallback for coalesced signals.
  const reaper = setInterval(reap, 20);
  reaper.unref();
  process.on('SIGCHLD', reap);
  // Report readiness after Vitest's worker has registered its listener.
  const own = process.listenerCount('message');
  const waitForWorker = setInterval(() => {
    if (process.listenerCount('message') > own) {
      clearInterval(waitForWorker);
      process.send({ __oximut_zygote__: 'ready', pid: process.pid });
    }
  }, 1);
}
