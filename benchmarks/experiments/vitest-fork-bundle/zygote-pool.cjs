// Benchmark-only: serve Vitest's fork workers from one restored-image zygote
// per launch shape instead of booting a fresh `node` for every worker. Each
// worker is still its own fresh OS process (fork() of the zygote); it gets its
// own IPC socket, its own environment, and Vitest's unmodified worker code.
// Requires OXIMUT_ZYGOTE_ADDON (benchmarks/experiments/zygote-fork/fork.c) and
// Linux. See benchmarks/profiling/zygote-worker.cjs for the zygote side.
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const v8 = require('node:v8');

const preload = path.resolve(__dirname, '../../profiling/zygote-worker.cjs');
const addon = process.env.OXIMUT_ZYGOTE_ADDON;
assert(addon && fs.existsSync(addon), 'OXIMUT_ZYGOTE_ADDON must name the built fork addon');
const zygotes = new Map();
const counters = {zygotes: 0, forks: 0, exits: 0, zygoteFailures: 0};
// Diagnostic event trace, one file per controller, when a directory is given.
const traceDir = process.env.OXIMUT_ZYGOTE_TRACE_DIR;
const trace = traceDir
  ? (...fields) => fs.appendFileSync(path.join(traceDir, `controller-${process.pid}.log`),
    `${(performance.now()).toFixed(1)} ${fields.join(' ')}\n`)
  : () => {};

// Node's `advanced` IPC framing: a 4-byte big-endian length, then a V8
// serialization whose host objects carry a tag (0: ArrayBufferView, 1: other).
class Serializer extends v8.DefaultSerializer {
  _writeHostObject(object) {
    if (ArrayBuffer.isView(object)) {
      this.writeUint32(0);
      return super._writeHostObject(object);
    }
    this.writeUint32(1);
    this.writeValue({...object});
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
function* decode(state, chunk) {
  state.pending = state.pending.length ? Buffer.concat([state.pending, chunk]) : chunk;
  while (state.pending.length >= 4) {
    const size = state.pending.readUInt32BE(0) + 4;
    if (state.pending.length < size) return;
    const deserializer = new Deserializer(state.pending.subarray(4, size));
    state.pending = state.pending.subarray(size);
    deserializer.readHeader();
    yield deserializer.readValue();
  }
}

class ZygoteChild extends EventEmitter {
  pid = undefined;
  exitCode = null;
  signalCode = null;
  stdout = null;
  stderr = null;
  connected = true;
  #socket;
  #queue = [];
  #pendingSignal;
  #status;
  #ended = false;

  attach(pid, socket) {
    trace('attach', this.traceId, pid, 'queued', this.#queue.length);
    this.pid = pid;
    this.#socket = socket;
    const state = {pending: Buffer.alloc(0)};
    this.decodeState = state;
    socket.on('data', (chunk) => {
      for (const message of decode(state, chunk)) {
        if (!this.seen) { this.seen = true; trace('first-message', this.traceId, message?.type); }
        if (traceDir) this.#remember('in', message, chunk.length);
        this.emit('message', message);
      }
    });
    // Like Node's channel, a failed read is a disconnect, not an error.
    socket.on('error', (error) => { trace('socket-error', this.traceId, error.code, error.syscall); if (error.syscall !== 'read') this.emit('error', error); });
    socket.on('close', () => {
      trace('socket-close', this.traceId);
      this.#ended = true;
      this.connected = false;
      this.emit('disconnect');
      this.#maybeExit();
    });
    for (const buffer of this.#queue) socket.write(buffer);
    this.#queue = [];
    if (this.#pendingSignal) this.kill(this.#pendingSignal);
  }

  // Node reports exit after the IPC messages a child wrote before exiting.
  exited(code, signal) {
    trace('status', this.traceId, code, signal);
    this.#status = {code, signal};
    this.#maybeExit();
  }

  #maybeExit() {
    if (!this.#status || (!this.#ended && this.#socket)) return;
    if (this.exitCode !== null || this.signalCode !== null) return;
    counters.exits++;
    trace('emit-exit', this.traceId);
    this.exitCode = this.#status.code;
    this.signalCode = this.#status.signal;
    this.emit('exit', this.exitCode, this.signalCode);
    this.emit('close', this.exitCode, this.signalCode);
  }

  send(message, ...rest) {
    const callback = rest.find((value) => typeof value === 'function');
    if (!this.connected) {
      const error = Object.assign(new Error('Channel closed'), {code: 'ERR_IPC_CHANNEL_CLOSED'});
      if (callback) process.nextTick(callback, error); else process.nextTick(() => this.emit('error', error));
      return false;
    }
    const buffer = encode(message);
    if (traceDir) this.#remember('out', message, buffer.length);
    if (this.#socket) return this.#socket.write(buffer, callback);
    this.#queue.push(buffer);
    if (callback) process.nextTick(callback, null);
    return true;
  }

  #recent = [];
  #state;
  #remember(direction, message, bytes) {
    const summary = message?.type ?? message?.m ?? message?.t ?? Object.keys(message ?? {}).join(',');
    this.#recent.push(`${performance.now().toFixed(1)}:${direction}:${summary}:${message?.i ?? ''}:${bytes}`);
    if (this.#recent.length > 12) this.#recent.shift();
  }

  // Diagnostic: what a worker that never finished was doing.
  #dumpStalled() {
    const read = (file) => { try { return fs.readFileSync(file, 'utf8').trim().replace(/\s+/g, ' '); } catch (e) { return e.code; } };
    const tasks = (() => { try { return fs.readdirSync(`/proc/${this.pid}/task`); } catch { return []; } })();
    trace('stalled', this.traceId, 'state', read(`/proc/${this.pid}/stat`).split(' ')[2],
      'threads', tasks.length, 'pendingBytes', this.decodeState?.pending.length ?? -1,
      'writable', this.#socket?.writableLength ?? -1);
    for (const tid of tasks) {
      trace('stalled-task', this.traceId, tid, 'wchan', read(`/proc/${this.pid}/task/${tid}/wchan`),
        'syscall', read(`/proc/${this.pid}/task/${tid}/syscall`), 'comm', read(`/proc/${this.pid}/task/${tid}/comm`));
    }
    trace('stalled-recent', this.traceId, ...this.#recent);
    const zygotePid = this.traceId.split(':')[0];
    const zygoteTasks = (() => { try { return fs.readdirSync(`/proc/${zygotePid}/task`); } catch { return []; } })();
    trace('zygote-threads', zygotePid, ...zygoteTasks.map((tid) => `${tid}=${read(`/proc/${zygotePid}/task/${tid}/comm`)}`));
    // A symbolized native stack (the controller is an ancestor, so YAMA allows it).
    const result = require('node:child_process').spawnSync('gdb', ['-p', String(this.pid), '-batch', '-nx',
      '-ex', 'set pagination off', '-ex', 'info threads', '-ex', 'thread apply all bt 40',
      '-ex', 'info symbol $rdi', '-ex', 'x/8gx $rdi'], {encoding: 'utf8', timeout: 20_000});
    fs.writeFileSync(path.join(traceDir, `stack-${this.pid}.txt`),
      `${result.error ?? ''}\n${result.stdout ?? ''}\n${result.stderr ?? ''}`);
  }

  kill(signal = 'SIGTERM') {
    trace('kill', this.traceId, signal, this.pid);
    if (traceDir && signal === 'SIGKILL' && this.pid !== undefined && !this.#status) this.#dumpStalled();
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
    this.#socket?.end();
  }

  ref() {}
  unref() {}
}

function startZygote(original, entry, args, options) {
  const env = {...options.env};
  env.OXIMUT_ZYGOTE_ADDON = addon;
  env.NODE_OPTIONS = `${env.NODE_OPTIONS ?? ''} --require=${JSON.stringify(preload)}`.trim();
  const process_ = Reflect.apply(original, require('node:child_process'), [entry, args, {
    ...options, env, stdio: 'pipe', serialization: 'advanced',
    execArgv: [...options.execArgv, '--single-threaded'],
  }]);
  counters.zygotes++;
  // Children inherit the zygote's stdio, so their output arrives here.
  process_.stdout.pipe(process.stdout, {end: false});
  process_.stderr.pipe(process.stderr, {end: false});
  const zygote = {process: process_, ready: false, nextId: 0, waiting: [], children: new Map()};
  process_.on('message', (message, handle) => {
    const kind = message?.__oximut_zygote__;
    trace('zygote-message', process_.pid, kind, message?.id ?? '');
    if (kind === 'ready') {
      zygote.ready = true;
      for (const request of zygote.waiting) process_.send(request);
      zygote.waiting = [];
    } else if (kind === 'spawned') {
      counters.forks++;
      zygote.children.get(message.id).attach(message.pid, handle);
    } else if (kind === 'exit') {
      const child = zygote.children.get(message.id);
      zygote.children.delete(message.id);
      child.exited(message.code, message.signal);
    }
  });
  process_.on('exit', (code, signal) => {
    trace('zygote-exit', process_.pid, code, signal, zygote.children.size);
    for (const child of zygote.children.values()) {
      counters.zygoteFailures++;
      child.emit('error', new Error(`zygote exited ${code}/${signal} before its worker`));
      child.exited(1, null);
    }
    zygote.children.clear();
  });
  return zygote;
}

function zygoteFork(original, entry, args, options) {
  const key = JSON.stringify([entry, args ?? [], options.execArgv ?? [], options.cwd ?? null]);
  let zygote = zygotes.get(key);
  if (!zygote || zygote.process.exitCode !== null || zygote.process.signalCode !== null) {
    zygote = startZygote(original, entry, args ?? [], options);
    zygotes.set(key, zygote);
  }
  const id = zygote.nextId++;
  const child = new ZygoteChild();
  child.traceId = `${zygote.process.pid}:${id}`;
  trace('spawn-request', child.traceId, zygote.ready, options.env?.OXIMUT_ACTIVE_MUTANT?.slice(0, 8));
  zygote.children.set(id, child);
  const request = {__oximut_zygote__: 'spawn', id, env: options.env};
  if (zygote.ready) zygote.process.send(request); else zygote.waiting.push(request);
  return child;
}

globalThis[Symbol.for('oximut.diagnostic.residentHelpers')] = () => [...zygotes.values()]
  .filter(({process: zygote}) => zygote.exitCode === null && zygote.signalCode === null)
  .map(({process: zygote}) => zygote.pid);

process.on('exit', () => {
  for (const zygote of zygotes.values()) zygote.process.kill('SIGKILL');
  const directory = process.env.OXIMUT_PROJECT_BUNDLE_DIR;
  if (directory) {
    fs.writeFileSync(path.join(directory, `zygote-${process.pid}.json`), JSON.stringify(counters), {flag: 'wx'});
  }
});

module.exports = {zygoteFork, counters, encode, decode};
