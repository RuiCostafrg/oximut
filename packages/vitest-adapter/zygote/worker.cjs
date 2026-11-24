'use strict';
// Preload for the zygote: one warm Vitest fork worker that stops being a worker
// and forks a fresh child per spawn request. pool.cjs starts it with
// --single-threaded and `NODE_OPTIONS=--require=<this file>`, with
// OXIMUT_ZYGOTE_ADDON naming the built fork addon (native/zygote-fork.c). Once
// Vitest has registered its worker listener the zygote reports ready. Each
// child gets its own IPC channel and then runs the unmodified Vitest worker.
// What a forked Node child needs is documented in
// benchmarks/experiments/zygote-fork/README.md.
const addonPath = process.env.OXIMUT_ZYGOTE_ADDON;
if (addonPath) {
  const net = require('node:net');
  const { _forkChild } = require('node:child_process');
  const { signals } = require('node:os').constants;
  const addon = require(addonPath);
  delete process.env.OXIMUT_ZYGOTE_ADDON;
  // Opt-in diagnostic timeline (OXIMUT_TRACE_TIMELINE); see ../timeline.cjs.
  const trace = process.env.OXIMUT_TRACE_TIMELINE ? require('../timeline.cjs') : undefined;
  const signalNames = Object.fromEntries(
    Object.entries(signals).map(([name, number]) => [number, name]),
  );
  // A precollect template reports whether its collect left anything a fork must not
  // share (see quiescence.cjs). It listens before the readiness count below.
  const quiescence =
    process.env.OXIMUT_ZYGOTE_WARM === '1' && process.env.OXIMUT_ZYGOTE_PRECOLLECT_GUARD !== 'off'
      ? require('./quiescence.cjs').watchCollect()
      : undefined;
  const children = new Map();
  const queue = [];
  let inflight = false;

  const reap = () => {
    for (;;) {
      const result = addon.waitpidNohang();
      if (!result) return;
      const id = children.get(result.pid);
      children.delete(result.pid);
      if (id !== undefined) {
        process.send({
          __oximut_zygote__: 'exit',
          id,
          pid: result.pid,
          code: result.code,
          signal: result.signal === null ? null : (signalNames[result.signal] ?? result.signal),
        });
      }
    }
  };

  const becomeChild = (parentEnd, childEnd, env, id) => {
    process.removeListener('message', onZygoteMessage);
    process.removeListener('internalMessage', onInternalMessage);
    process.removeAllListeners('disconnect');
    process.removeAllListeners('SIGCHLD');
    clearInterval(reaper);
    quiescence?.dispose();
    // Node cached the zygote's pid at bootstrap.
    Object.defineProperty(process, 'pid', {
      value: addon.getpid(),
      configurable: true,
      enumerable: true,
    });
    if (trace) trace.emit('worker', 'fork_entered', { id, mutant: env?.OXIMUT_ACTIVE_MUTANT });
    // Node's exit path disposes the platform and joins worker threads that do
    // not exist after fork(), which hangs. Leave with _exit() once JavaScript
    // 'exit' handlers have run.
    process.reallyExit = (code) => addon._exitNow(code | 0);
    // A requested environment replaces the zygote's, as fork({env}) would.
    if (env) {
      for (const key of Object.keys(process.env))
        if (!Object.hasOwn(env, key)) delete process.env[key];
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
      // Vitest's message listener predates this channel, so hold a reference.
      process.channel.ref();
      if (trace) process.once('message', () => trace.emit('worker', 'first_message', { id }));
      process.on('exit', (code) => addon._exitNow(code | 0));
    });
    process.disconnect();
  };

  const spawnNext = () => {
    if (inflight || queue.length === 0) return;
    inflight = true;
    // fork() returns -2 while a V8 worker thread is busy (see zygote-fork.c).
    const [parentEnd, childEnd] = addon.socketpair();
    if (trace) {
      const [{ id, env }] = queue;
      trace.emit('zygote', 'fork_begin', { id, mutant: env?.OXIMUT_ACTIVE_MUTANT });
    }
    const pid = addon.fork();
    if (pid === -2) {
      if (trace) trace.emit('zygote', 'fork_busy', { id: queue[0].id });
      addon.closeFd(parentEnd);
      addon.closeFd(childEnd);
      inflight = false;
      setTimeout(spawnNext, 1);
      return;
    }
    const { id, env } = queue.shift();
    if (pid === 0) return becomeChild(parentEnd, childEnd, env, id);
    addon.closeFd(childEnd);
    children.set(pid, id);
    if (trace)
      trace.emit('zygote', 'forked', { id, workerPid: pid, mutant: env?.OXIMUT_ACTIVE_MUTANT });
    // Never read here: the child's first messages belong to the controller.
    const socket = new net.Socket({
      fd: parentEnd,
      readable: true,
      writable: true,
      manualStart: true,
    });
    process.send({ __oximut_zygote__: 'spawned', id, pid }, socket);
  };

  const onInternalMessage = (message) => {
    if (message?.cmd === 'NODE_HANDLE_ACK' || message?.cmd === 'NODE_HANDLE_NACK') {
      inflight = false;
      setImmediate(spawnNext);
    }
  };

  const onZygoteMessage = (message) => {
    if (message?.__oximut_zygote__ === 'spawn') {
      if (trace)
        trace.emit('zygote', 'spawn_received', {
          id: message.id,
          mutant: message.env?.OXIMUT_ACTIVE_MUTANT,
        });
      queue.push(message);
      spawnNext();
    } else if (message?.__oximut_zygote__ === 'shutdown') {
      process.exit(0);
    }
  };

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
