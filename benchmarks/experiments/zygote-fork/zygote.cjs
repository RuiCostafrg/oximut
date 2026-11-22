'use strict';
// Zygote fork server. Started by controller.mjs via child_process.fork().
//
// It warms up the runtime once, then forks a fresh OS process per job. The
// child inherits the warmed V8 heap (copy-on-write) and a new Node IPC channel
// on a socketpair end, which is handed to the controller.

const net = require('net');
const path = require('path');
const { _forkChild } = require('child_process');

const addonPath =
  process.env.ZYGOTE_ADDON || path.join(__dirname, 'build', 'fork.node');
const addon = require(addonPath);

const trace = process.env.ZYGOTE_TRACE === '1'
  ? (...a) => require('fs').writeSync(2, `[z ${process.pid}] ${a.join(' ')}\n`) : () => {};
let reapTimer = null;
let shuttingDown = false;
let ready = false;

/** pid -> job id, for children we have not reaped yet. */
const children = new Map();

/** Spawns are serialized until the controller ACKs the passed handle. */
const spawnQueue = [];
let inflight = null;
let inflightTimer = null;

function log(...args) {
  // stderr is inherited by the controller; keep stdout clean.
  console.error('[zygote]', ...args);
}

async function warm() {
  const builtins = [
    'fs', 'path', 'os', 'util', 'crypto', 'v8', 'perf_hooks', 'events',
    'stream', 'net', 'child_process', 'buffer', 'url', 'assert', 'timers',
  ];
  for (const name of builtins) require(name);

  let vitest = false;
  try {
    await import('vitest');
    vitest = true;
  } catch {
    try {
      const resolved = require.resolve('vitest/dist/index.js');
      await import(require('url').pathToFileURL(resolved).href);
      vitest = true;
    } catch {
      // vitest is optional; the prototype still runs without it.
    }
  }
  return vitest;
}

function applyEnv(env) {
  if (!env || typeof env !== 'object') return;
  for (const key of Object.keys(env)) process.env[key] = String(env[key]);
}

function onMessage(msg) {
  if (!msg || typeof msg !== 'object') return;
  if (msg.type === 'spawn') enqueueSpawn(msg);
  else if (msg.type === 'shutdown') shutdown();
}

function enqueueSpawn(msg) {
  spawnQueue.push(msg);
  drainSpawnQueue();
}

function drainSpawnQueue() {
  if (shuttingDown || inflight || spawnQueue.length === 0) return;
  const msg = spawnQueue.shift();
  inflight = msg;

  let fds;
  try {
    fds = addon.socketpair();
  } catch (err) {
    finishInflight();
    sendToController({ type: 'spawn-error', id: msg.id, error: String(err) });
    return;
  }
  const [parentEnd, childEnd] = fds;
  trace('fork', msg.id);
  // -2: a V8 worker thread is busy; fork only once they all sleep.
  let pid;
  while ((pid = addon.fork()) === -2);
  trace('forked', pid);

  if (pid === 0) {
    // ---- child ----
    childMain(msg, parentEnd, childEnd);
    return;
  }

  // ---- zygote ----
  addon.closeFd(childEnd);
  children.set(pid, msg.id);

  let sock;
  try {
    // Never read here: the child's first messages must reach the controller.
    sock = new net.Socket({ fd: parentEnd, readable: true, writable: true, manualStart: true });
    process.send({ type: 'spawned', id: msg.id, pid }, sock, (err) => {
      if (err) {
        sendToController({
          type: 'spawn-error',
          id: msg.id,
          pid,
          error: String(err),
        });
      }
    });
  } catch (err) {
    try { addon.closeFd(parentEnd); } catch {}
    sendToController({
      type: 'spawn-error',
      id: msg.id,
      pid,
      error: String(err),
    });
    finishInflight();
  }
}

function sendToController(msg) {
  try {
    process.send(msg);
  } catch {
    // controller is gone; nothing useful to do
  }
}

function finishInflight() {
  inflight = null;
  if (inflightTimer) {
    clearTimeout(inflightTimer);
    inflightTimer = null;
  }
  // Give the loop a chance to run uv_close() on the handle we just sent.
  setTimeout(drainSpawnQueue, 1);
}

function childMain(msg, parentEnd, childEnd) {
  // Do not run any more zygote bookkeeping in the child.
  if (reapTimer) clearInterval(reapTimer);
  reapTimer = null;
  process.removeListener('message', onMessage);
  process.removeListener('internalMessage', onInternalMessage);

  // Close the zygote's own IPC channel. This does not kill the child: it only
  // closes the duplicated channel fd (the controller sees EOF only when the
  // zygote itself exits).
  // Inherited zygote listeners must not run in the child (the zygote exits
  // when its controller channel disconnects).
  process.removeAllListeners('disconnect');
  // Node's process cached the zygote's pid at bootstrap.
  Object.defineProperty(process, 'pid', {value: addon.getpid(), configurable: true, enumerable: true, writable: false});
  // Normal Node exit disposes the platform and joins worker threads that do
  // not exist after fork(), which deadlocks. Run JS 'exit' handlers through
  // process.exit as usual, then leave with _exit().
  process.reallyExit = (code) => addon._exitNow(code | 0);
  trace('child disconnect');
  try { process.disconnect(); } catch {}
  trace('child disconnected');
  try { addon.closeFd(parentEnd); } catch {}

  // Give the new channel a non-inherited fd and attach it as a real Node IPC
  // channel using the same 'advanced' (v8.serialize) framing Node uses.
  addon.clearCloexec(childEnd);
  _forkChild(childEnd, 'advanced');
  trace('child attached');

  applyEnv(msg.env);

  const pid = addon.getpid();
  const ppid = addon.getppid();
  process.send({
    type: 'zygote-child-ready',
    id: msg.id,
    pid,
    ppid,
    // These are cached at process start and are *stale* after fork:
    processPid: process.pid,
    processPpid: process.ppid,
    cpuUsage: process.cpuUsage(),
    at: Date.now(),
  });

  const runJob = require('./job.cjs');
  Promise.resolve(runJob({ id: msg.id, env: msg.env || {}, addon })).catch(
    (err) => {
      const message = String((err && err.stack) || err);
      try {
        process.send(
          { type: 'job-error', id: msg.id, error: message },
          undefined,
          undefined,
          () => addon._exitNow(1),
        );
      } catch {
        addon._exitNow(1);
      }
    },
  );
}

function onInternalMessage(msg) {
  trace('internal', msg && msg.cmd);
  if (!msg || (msg.cmd !== 'NODE_HANDLE_ACK' && msg.cmd !== 'NODE_HANDLE_NACK'))
    return;
  if (!inflight) return;
  finishInflight();
}

function reap() {
  for (;;) {
    let result;
    try {
      result = addon.waitpidNohang();
    } catch (err) {
      log('waitpid failed:', err.message);
      return;
    }
    if (!result) return;
    const id = children.get(result.pid);
    children.delete(result.pid);
    if (id === undefined) continue;
    sendToController({
      type: 'exit',
      id,
      pid: result.pid,
      code: result.code,
      signal: result.signal,
    });
  }
}

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  if (reapTimer) clearInterval(reapTimer);
  reapTimer = null;
  spawnQueue.length = 0;
  if (inflightTimer) clearTimeout(inflightTimer);
  inflightTimer = null;
  inflight = null;

  reap();
  try { process.disconnect(); } catch {}

  if (children.size === 0) {
    // Original process: normal exit is safe here (only forked children hang).
    process.exit(0);
  } else {
    // Should not happen in the controller flow, but never leave zombies.
    const timer = setInterval(() => {
      reap();
      if (children.size === 0) {
        clearInterval(timer);
        process.exit(0);
      }
    }, 20);
  }
}

async function main() {
  const vitest = await warm();

  process.on('message', onMessage);
  process.on('internalMessage', onInternalMessage);
  process.on('disconnect', () => {
    if (!shuttingDown) process.exit(0);
  });

  reapTimer = setInterval(reap, 20);
  ready = true;
  process.send({
    type: 'ready',
    pid: addon.getpid(),
    processPid: process.pid,
    vitest,
    flags: process.execArgv,
  });
}

main().catch((err) => {
  log('fatal:', err && err.stack ? err.stack : err);
  process.exit(1);
});
