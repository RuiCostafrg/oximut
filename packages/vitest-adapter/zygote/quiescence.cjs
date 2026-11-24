'use strict';
// Quiescence guard for precollect templates (see pool.cjs, shared-lanes.ts warm()).
// A template runs a test file's top-level code once (Vitest collect) and every
// mutant's worker is then forked from it, so whatever that code left behind is
// shared by concurrent forks: directories made with mkdtemp, open servers and
// sockets, child processes, timers, requests in flight, a captured pid. The guard
// watches the collect only. It reports whether collect
//   - left active handles or requests beyond those it started with,
//   - left a timer pending,
//   - wrote or created files or directories, or
//   - started a child process.
// Any of these makes the template ineligible: its mutants take the ordinary
// zygote path, which is still correct, only slower. The verdict goes to the pool
// as a zygote message just before Vitest's own end-of-collect response, so the
// pool has it by the time it asks whether the template is ready.
const childProcess = require('node:child_process');
const fs = require('node:fs');
const { syncBuiltinESMExports } = require('node:module');
const timers = require('node:timers');
const { promisify } = require('node:util');

// `fs` calls that write, create, move or delete something. The same names exist
// on fs.promises.
const FS_WRITES = [
  'writeFile',
  'appendFile',
  'mkdir',
  'mkdtemp',
  'rename',
  'copyFile',
  'cp',
  'rm',
  'rmdir',
  'unlink',
  'symlink',
  'link',
  'truncate',
];
const FS_STREAMS = ['createWriteStream'];
const CHILD_PROCESSES = [
  'spawn',
  'spawnSync',
  'exec',
  'execSync',
  'execFile',
  'execFileSync',
  'fork',
];
const MAX_RECORDED = 16;

const { O_WRONLY, O_RDWR, O_CREAT, O_APPEND, O_TRUNC } = fs.constants;
const WRITE_FLAGS = O_WRONLY | O_RDWR | O_CREAT | O_APPEND | O_TRUNC;

/** Whether `open` flags (a string such as 'a+' or a number) allow writing. */
function opensForWriting(flags) {
  if (flags === undefined || flags === null) return false;
  if (typeof flags === 'number') return (flags & WRITE_FLAGS) !== 0;
  return /[wa+]/.test(String(flags));
}

function describeTarget(value) {
  if (typeof value === 'string') return value.slice(0, 200);
  if (value instanceof URL) return value.href.slice(0, 200);
  if (Buffer.isBuffer(value)) return value.toString().slice(0, 200);
  return undefined;
}

function counts(list) {
  const result = new Map();
  for (const item of list) result.set(item, (result.get(item) ?? 0) + 1);
  return result;
}

/**
 * Watch one collect. `begin()` installs the instrumentation and snapshots the
 * process; `finish()` removes it and returns the verdict:
 * `{ eligible, reasons: { handles: [...], timers: [...], fsWrites: [...],
 * children: [...] } }`, each reason a short string.
 */
function createGuard() {
  let restore = [];
  let before;
  let fsWrites = [];
  let children = [];
  let pendingTimers = new Map();

  const record = (list, kind, target) => {
    if (list.length < MAX_RECORDED) list.push(target ? `${kind} ${target}` : kind);
  };
  // Replace object[name] with a function that calls `note(args)` first. Properties of
  // the original (util.promisify.custom) stay, and so does a custom promisified form.
  const patch = (object, name, note) => {
    const original = object[name];
    if (typeof original !== 'function') return;
    const wrapper = function (...args) {
      note(args);
      return Reflect.apply(original, this, args);
    };
    inherit(wrapper, original, note);
    object[name] = wrapper;
    restore.push(() => {
      object[name] = original;
    });
  };
  const patchTimer = (owner, name, wrap) => {
    const original = owner[name];
    const wrapper = wrap(original);
    inherit(wrapper, original);
    owner[name] = wrapper;
    restore.push(() => {
      owner[name] = original;
    });
  };

  function install() {
    for (const name of FS_WRITES) {
      for (const method of [name, `${name}Sync`])
        patch(fs, method, (args) => record(fsWrites, method, describeTarget(args[0])));
      patch(fs.promises, name, (args) =>
        record(fsWrites, `promises.${name}`, describeTarget(args[0])),
      );
    }
    for (const name of FS_STREAMS)
      patch(fs, name, (args) => record(fsWrites, name, describeTarget(args[0])));
    for (const name of ['open', 'openSync'])
      patch(fs, name, (args) => {
        if (opensForWriting(args[1])) record(fsWrites, name, describeTarget(args[0]));
      });
    patch(fs.promises, 'open', (args) => {
      if (opensForWriting(args[1])) record(fsWrites, 'promises.open', describeTarget(args[0]));
    });
    for (const name of CHILD_PROCESSES)
      patch(childProcess, name, (args) => record(children, name, describeTarget(args[0])));
    // Timers: a timeout still pending or any interval not cleared. Each is wrapped so
    // that firing or clearing forgets it. Handles that are unref'd never show in the
    // active resources, so they are only caught here.
    for (const owner of new Set([globalThis, timers])) {
      patchTimer(
        owner,
        'setTimeout',
        (original) =>
          function (callback, ...rest) {
            if (typeof callback !== 'function')
              return Reflect.apply(original, this, [callback, ...rest]);
            let handle;
            const wrapped = function (...args) {
              pendingTimers.delete(handle);
              return Reflect.apply(callback, this, args);
            };
            handle = Reflect.apply(original, this, [wrapped, ...rest]);
            pendingTimers.set(handle, 'setTimeout');
            return handle;
          },
      );
      patchTimer(
        owner,
        'setInterval',
        (original) =>
          function (...args) {
            const handle = Reflect.apply(original, this, args);
            pendingTimers.set(handle, 'setInterval');
            return handle;
          },
      );
      for (const name of ['clearTimeout', 'clearInterval'])
        patchTimer(
          owner,
          name,
          (original) =>
            function (handle, ...rest) {
              pendingTimers.delete(handle);
              return Reflect.apply(original, this, [handle, ...rest]);
            },
        );
    }
    // Code that imported these by name sees the wrappers too.
    syncBuiltinESMExports();
  }

  function uninstall() {
    for (const undo of restore.reverse()) undo();
    restore = [];
    syncBuiltinESMExports();
  }

  return {
    begin() {
      fsWrites = [];
      children = [];
      pendingTimers = new Map();
      // Standard streams are created lazily; do not count their handles.
      void process.stdout;
      void process.stderr;
      before = counts(process.getActiveResourcesInfo?.() ?? []);
      install();
    },
    finish() {
      uninstall();
      const handles = [];
      const after = counts(process.getActiveResourcesInfo?.() ?? []);
      for (const [type, count] of after) {
        const extra = count - (before?.get(type) ?? 0);
        if (extra > 0) handles.push(extra > 1 ? `${type} x${extra}` : type);
      }
      const reasons = {
        handles,
        timers: [...new Set(pendingTimers.values())],
        fsWrites,
        children,
      };
      pendingTimers = new Map();
      const eligible = Object.values(reasons).every((list) => list.length === 0);
      return { eligible, reasons };
    },
  };
}

/** Keep the original's own properties; its promisified form is noted like the call. */
function inherit(wrapper, original, note) {
  for (const key of Reflect.ownKeys(original)) {
    if (key === 'length' || key === 'name' || key === 'prototype') continue;
    const descriptor = Object.getOwnPropertyDescriptor(original, key);
    if (key === promisify.custom && note && typeof descriptor.value === 'function') {
      const custom = descriptor.value;
      descriptor.value = function (...args) {
        note(args);
        return Reflect.apply(custom, this, args);
      };
    }
    Object.defineProperty(wrapper, key, descriptor);
  }
}

/**
 * Hook a template's Vitest worker: the guard runs between its `collect` request
 * and its `testfileFinished` response, and its verdict is sent before that
 * response.
 */
function watchCollect() {
  const guard = createGuard();
  let watching = false;
  const onMessage = (message) => {
    if (message?.type === 'collect' && !watching) {
      watching = true;
      guard.begin();
    }
  };
  process.on('message', onMessage);
  const original = process.send;
  process.send = function oximutQuiescenceSend(message, ...rest) {
    if (watching && message?.__vitest_worker_response__ && message.type === 'testfileFinished') {
      watching = false;
      Reflect.apply(original, this, [{ __oximut_zygote__: 'quiescence', ...guard.finish() }]);
    }
    return Reflect.apply(original, this, [message, ...rest]);
  };
  return {
    // A forked child is not a template: stop listening, leave the send wrapper inert.
    dispose() {
      process.removeListener('message', onMessage);
      if (watching) guard.finish();
      watching = false;
    },
  };
}

module.exports = { watchCollect, createGuard };
