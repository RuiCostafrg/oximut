'use strict';
// Opt-in zygote workers (OXIMUT_ZYGOTE=1, Linux only). Serve Vitest's fork
// workers for mutants from one warm zygote per launch shape instead of booting
// a fresh `node` per worker. Every worker is still its own OS process (fork()
// of the zygote) with its own IPC socket, its own environment and Vitest's
// unmodified worker code. worker.cjs is the zygote side.
//
// install() scopes a patch of child_process.fork to Vitest's fork entry with
// OXIMUT_ACTIVE_MUTANT in its env, so baseline and coverage workers stay stock.
// A mutant must never fail because of the zygote: if it is unavailable, dies or
// stalls, children not yet attached are served by a real fork and the zygote
// is disabled for the rest of this controller's life.
const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const { syncBuiltinESMExports } = require('node:module');
const path = require('node:path');
const v8 = require('node:v8');

const FORKS_ENTRY = '/vitest/dist/workers/forks.js';
const preload = path.join(__dirname, 'worker.cjs');
const defaultAddon = path.resolve(__dirname, '../native/build/zygote-fork.node');
const zygotes = new Map();
const counters = { zygotes: 0, forks: 0, exits: 0, zygoteFailures: 0, fallbacks: 0 };
// Set by install(): { addon, original, disabled }.
let state;
// Mutant ids whose workers must be real forks while their job runs (the CLI's
// zygote watchdog). Nothing else changes: the zygote and its other children stay.
let bypassed = new Set();

function readyTimeoutMs() {
  // The override exists for tests; the default is the contract.
  const value = Number(process.env.OXIMUT_ZYGOTE_READY_TIMEOUT_MS);
  return Number.isInteger(value) && value > 0 ? value : 10_000;
}

function log(message) {
  process.stderr.write(`oximut: ${message}\n`);
}

// Node's `advanced` IPC framing: a 4-byte big-endian length, then a V8
// serialization whose host objects carry a tag (0: ArrayBufferView, 1: other).
class Serializer extends v8.DefaultSerializer {
  _writeHostObject(object) {
    if (ArrayBuffer.isView(object)) {
      this.writeUint32(0);
      return super._writeHostObject(object);
    }
    this.writeUint32(1);
    this.writeValue({ ...object });
  }
}
class Deserializer extends v8.DefaultDeserializer {
  _readHostObject() {
    const tag = this.readUint32();
    if (tag === 0) return super._readHostObject();
    assert.equal(tag, 1, 'unknown IPC host object tag');
    return this.readValue();
  }
}
function encode(message) {
  const serializer = new Serializer();
  serializer.writeRawBytes(Buffer.alloc(4));
  serializer.writeHeader();
  serializer.writeValue(message);
  const buffer = serializer.releaseBuffer();
  buffer.writeUInt32BE(buffer.length - 4, 0);
  return buffer;
}
function* decode(pending, chunk) {
  pending.buffer = pending.buffer.length ? Buffer.concat([pending.buffer, chunk]) : chunk;
  while (pending.buffer.length >= 4) {
    const size = pending.buffer.readUInt32BE(0) + 4;
    if (pending.buffer.length < size) return;
    const deserializer = new Deserializer(pending.buffer.subarray(4, size));
    pending.buffer = pending.buffer.subarray(size);
    deserializer.readHeader();
    yield deserializer.readValue();
  }
}

/**
 * The slice of ChildProcess that Vitest's ForksPoolWorker uses. It starts as a
 * zygote child (a forked process reached over a socket the zygote passed us)
 * and may instead adopt a real ChildProcess if the zygote fails first.
 */
class ZygoteChild extends EventEmitter {
  pid = undefined;
  exitCode = null;
  signalCode = null;
  stdout = null;
  stderr = null;
  connected = true;
  attachTimer = undefined;
  #launch;
  #socket;
  #real;
  #queue = [];
  #pendingSignal;
  #pendingDisconnect = false;
  #status;
  #ended = false;

  constructor(launch) {
    super();
    this.#launch = launch;
  }

  get attached() {
    return this.#socket !== undefined;
  }

  attach(pid, socket) {
    this.pid = pid;
    this.#socket = socket;
    const pending = { buffer: Buffer.alloc(0) };
    socket.on('data', (chunk) => {
      for (const message of decode(pending, chunk)) this.emit('message', message);
    });
    // Like Node's channel, a failed read is a disconnect, not an error.
    socket.on('error', (error) => {
      if (error.syscall !== 'read') this.emit('error', error);
    });
    socket.on('close', () => {
      this.#ended = true;
      this.connected = false;
      this.emit('disconnect');
      this.#maybeExit();
    });
    for (const message of this.#queue) socket.write(encode(message));
    this.#queue = [];
    if (this.#pendingDisconnect) socket.end();
    if (this.#pendingSignal) this.kill(this.#pendingSignal);
  }

  // The zygote failed before this child attached: serve it with a real fork.
  fallBack() {
    const { entry, args, options } = this.#launch;
    let real;
    try {
      real = Reflect.apply(state.original, childProcess, [entry, args, options]);
    } catch (error) {
      this.connected = false;
      process.nextTick(() => {
        this.emit('error', error);
        this.exited(1, null);
      });
      return;
    }
    this.#real = real;
    this.pid = real.pid;
    // The zygote's children inherit its stdio, which the pool already pipes.
    real.stdout?.pipe(process.stdout, { end: false });
    real.stderr?.pipe(process.stderr, { end: false });
    real.on('message', (...rest) => this.emit('message', ...rest));
    real.on('error', (error) => this.emit('error', error));
    real.on('disconnect', () => {
      this.connected = false;
      this.emit('disconnect');
    });
    real.on('exit', (code, signal) => {
      this.exitCode = code;
      this.signalCode = signal;
      this.emit('exit', code, signal);
    });
    real.on('close', (code, signal) => this.emit('close', code, signal));
    for (const message of this.#queue) real.send(message);
    this.#queue = [];
    if (this.#pendingDisconnect) real.disconnect();
    if (this.#pendingSignal) real.kill(this.#pendingSignal);
  }

  // The zygote died after this child attached. Its exit status is lost with
  // the zygote that reaped it, so end the child and report a killed worker.
  abandon() {
    if (this.#real || this.#status) return;
    try {
      process.kill(this.pid, 'SIGKILL');
    } catch {}
    this.exited(null, 'SIGKILL');
    this.#socket?.destroy();
  }

  // Node reports exit after the IPC messages a child wrote before exiting.
  exited(code, signal) {
    this.#status = { code, signal };
    this.#maybeExit();
  }

  #maybeExit() {
    if (!this.#status || (!this.#ended && this.#socket)) return;
    if (this.exitCode !== null || this.signalCode !== null) return;
    counters.exits++;
    this.exitCode = this.#status.code;
    this.signalCode = this.#status.signal;
    this.emit('exit', this.exitCode, this.signalCode);
    this.emit('close', this.exitCode, this.signalCode);
  }

  send(message, ...rest) {
    if (this.#real) return this.#real.send(message, ...rest);
    const callback = rest.find((value) => typeof value === 'function');
    if (!this.connected) {
      const error = Object.assign(new Error('Channel closed'), { code: 'ERR_IPC_CHANNEL_CLOSED' });
      if (callback) process.nextTick(callback, error);
      else process.nextTick(() => this.emit('error', error));
      return false;
    }
    if (this.#socket) return this.#socket.write(encode(message), callback);
    this.#queue.push(message);
    if (callback) process.nextTick(callback, null);
    return true;
  }

  kill(signal = 'SIGTERM') {
    if (this.#real) return this.#real.kill(signal);
    if (this.exitCode !== null || this.signalCode !== null || this.#status) return false;
    if (this.pid === undefined) {
      this.#pendingSignal = signal;
      return true;
    }
    try {
      process.kill(this.pid, signal);
      return true;
    } catch {
      return false;
    }
  }

  disconnect() {
    if (this.#real) this.#real.disconnect();
    else if (this.#socket) this.#socket.end();
    else this.#pendingDisconnect = true;
  }

  ref() {
    this.#real?.ref();
  }

  unref() {
    this.#real?.unref();
  }
}

// One failure ends the zygote for this controller: disable it, kill it, and
// settle every child it still owed us.
function fail(zygote, reason) {
  if (zygote.failed) return;
  zygote.failed = true;
  clearTimeout(zygote.readyTimer);
  counters.zygoteFailures++;
  if (!state.disabled) {
    state.disabled = true;
    log(`zygote workers disabled after a zygote failure (${reason}); using normal workers`);
  }
  try {
    zygote.process.kill('SIGKILL');
  } catch {}
  const children = [...zygote.children.values()];
  zygote.children.clear();
  zygote.waiting = [];
  for (const child of children) {
    clearTimeout(child.attachTimer);
    if (child.attached) {
      child.abandon();
    } else {
      counters.fallbacks++;
      child.fallBack();
    }
  }
}

function request(zygote, child, message) {
  // A zygote that never answers a spawn request is as bad as one that died.
  child.attachTimer = setTimeout(
    () => fail(zygote, `no worker attached within ${readyTimeoutMs()} ms`),
    readyTimeoutMs(),
  );
  child.attachTimer.unref();
  zygote.process.send(message, (error) => {
    if (error) fail(zygote, `cannot reach the zygote: ${error.message}`);
  });
}

function startZygote(entry, args, options) {
  const env = { ...options.env, OXIMUT_ZYGOTE_ADDON: state.addon };
  env.NODE_OPTIONS = `${env.NODE_OPTIONS ?? ''} --require=${JSON.stringify(preload)}`.trim();
  const process_ = Reflect.apply(state.original, childProcess, [
    entry,
    args,
    {
      ...options,
      env,
      stdio: 'pipe',
      serialization: 'advanced',
      execArgv: [...(options.execArgv ?? []), '--single-threaded'],
    },
  ]);
  counters.zygotes++;
  // Children inherit the zygote's stdio, so their output arrives here.
  process_.stdout.pipe(process.stdout, { end: false });
  process_.stderr.pipe(process.stderr, { end: false });
  const zygote = {
    process: process_,
    ready: false,
    failed: false,
    nextId: 0,
    waiting: [],
    children: new Map(),
    readyTimer: undefined,
  };
  zygote.readyTimer = setTimeout(
    () => fail(zygote, `not ready within ${readyTimeoutMs()} ms`),
    readyTimeoutMs(),
  );
  zygote.readyTimer.unref();
  process_.on('message', (message, handle) => {
    const kind = message?.__oximut_zygote__;
    if (kind === 'ready') {
      clearTimeout(zygote.readyTimer);
      zygote.ready = true;
      const waiting = zygote.waiting;
      zygote.waiting = [];
      for (const { child, message: pending } of waiting) request(zygote, child, pending);
    } else if (kind === 'spawned') {
      const child = zygote.children.get(message.id);
      if (!child) {
        // The zygote failed first: nobody owns this worker any more.
        handle?.destroy();
        try {
          process.kill(message.pid, 'SIGKILL');
        } catch {}
        return;
      }
      clearTimeout(child.attachTimer);
      if (!handle) return fail(zygote, 'a spawned worker carried no IPC socket');
      counters.forks++;
      child.attach(message.pid, handle);
    } else if (kind === 'exit') {
      const child = zygote.children.get(message.id);
      zygote.children.delete(message.id);
      child?.exited(message.code, message.signal);
    }
  });
  process_.on('error', (error) => fail(zygote, `cannot start: ${error.message}`));
  process_.on('exit', (code, signal) => fail(zygote, `exited ${code}/${signal}`));
  return zygote;
}

function zygoteFork(entry, args, options) {
  if (state.disabled) {
    counters.fallbacks++;
    return Reflect.apply(state.original, childProcess, [entry, args, options]);
  }
  const key = JSON.stringify([entry, args, options.execArgv ?? [], options.cwd ?? null]);
  let zygote = zygotes.get(key);
  if (!zygote) {
    zygote = startZygote(entry, args, options);
    zygotes.set(key, zygote);
  }
  const id = zygote.nextId++;
  const child = new ZygoteChild({ entry, args, options });
  zygote.children.set(id, child);
  const message = { __oximut_zygote__: 'spawn', id, env: options.env };
  if (zygote.ready) request(zygote, child, message);
  else zygote.waiting.push({ child, message });
  return child;
}

function unavailableReason(addon) {
  if (process.platform !== 'linux') return `unsupported platform ${process.platform}`;
  if (!fs.existsSync(addon)) return `addon not found at ${addon}`;
  try {
    require(addon);
  } catch (error) {
    return `addon failed to load: ${error.message}`;
  }
  return undefined;
}

/**
 * Serve mutant Vitest fork workers from a zygote. Returns whether the patch
 * was installed; when the zygote cannot work here, one stderr line names why
 * and workers stay stock. Call once per controller.
 */
function install() {
  if (state) return !state.disabled;
  const addon = path.resolve(process.env.OXIMUT_ZYGOTE_ADDON || defaultAddon);
  const countersDir = process.env.OXIMUT_ZYGOTE_COUNTERS_DIR;
  if (countersDir) {
    process.on('exit', () => {
      try {
        fs.writeFileSync(
          path.join(countersDir, `zygote-${process.pid}.json`),
          JSON.stringify(counters),
        );
      } catch {}
    });
  }
  const reason = unavailableReason(addon);
  if (reason) {
    state = { addon, original: undefined, disabled: true };
    log(`zygote workers unavailable (${reason}); using normal workers`);
    return false;
  }
  const original = childProcess.fork;
  state = { addon, original, disabled: false };
  childProcess.fork = function oximutZygoteFork(entry, args, options) {
    if (
      typeof entry === 'string' &&
      entry.endsWith(FORKS_ENTRY) &&
      Array.isArray(args) &&
      options?.env?.OXIMUT_ACTIVE_MUTANT &&
      !bypassed.has(options.env.OXIMUT_ACTIVE_MUTANT)
    )
      return zygoteFork(entry, args, options);
    return Reflect.apply(original, this, [entry, args, options]);
  };
  // Vitest imports `fork` by name; refresh the live ESM binding.
  syncBuiltinESMExports();
  process.on('exit', () => {
    for (const zygote of zygotes.values()) zygote.process.kill('SIGKILL');
  });
  return true;
}

// Live zygote PIDs. They outlive a job on purpose; the CLI must admit them.
function residentHelpers() {
  return [...zygotes.values()]
    .filter(
      ({ failed, process: zygote }) =>
        !failed && zygote.exitCode === null && zygote.signalCode === null,
    )
    .map(({ process: zygote }) => zygote.pid);
}

/** Serve workers of exactly these mutants with real forks until changed. */
function bypass(mutantIds) {
  bypassed = new Set(mutantIds);
}

module.exports = { install, residentHelpers, bypass, counters };
