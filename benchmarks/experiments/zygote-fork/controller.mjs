// Correctness controller for the zygote fork-server prototype.
//
// Run with: node --expose-internals controller.mjs
//
// The controller-side child channels are real Node IPC channels: the zygote
// passes a socketpair end, we dup() it, wrap it in a pipe_wrap Pipe, and call
// the internal setupChannel() with 'advanced' serialization. That is exactly
// the framing _forkChild() uses in the child, so Map/BigInt survive.

import { fork } from 'node:child_process';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { setupChannel } = require('internal/child_process');
const { Pipe, constants } = process.binding('pipe_wrap');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const addonPath =
  process.env.ZYGOTE_ADDON || path.join(__dirname, 'build', 'fork.node');
const addon = require(addonPath);

const N = 12;
const CONCURRENCY = 4;
const FLAGS = process.env.ZYGOTE_FLAGS !== undefined
  ? process.env.ZYGOTE_FLAGS.split(' ').filter(Boolean)
  : ['--single-threaded', '--expose-gc'];
const KILL_ID = 7;
const EXIT7_ID = 5;

const checks = {
  noHang: false,
  zygoteReady: false,
  vitestPreloaded: false,
  allJobsSpawned: false,
  allPidsDistinct: false,
  pidsDifferFromZygote: false,
  handleForEveryJob: false,
  childPidMatchesSpawned: false,
  childPpidIsZygote: false,
  processPidMatchesFork: false,
  processPpidIsZygote: false,
  allJobsExited: false,
  exitCodesCorrect: false,
  nonKilledResultsComplete: false,
  gcObserved: false,
  threadpoolWorked: false,
  advancedSerialization: false,
  promiseAndTimerWorked: false,
  zygoteNoChildren: false,
  noZombies: false,
  zygoteCleanExit: false,
};

const jobs = Array.from({ length: N }, (_, id) => ({
  id,
  kill: id === KILL_ID,
  env:
    id === EXIT7_ID
      ? { JOB_EXIT: '7' }
      : id === KILL_ID
        ? { JOB_HANG: '1' }
        : {},
}));

const spawned = new Map(); // id -> pid
const channels = new Map(); // id -> EventEmitter target
const results = new Map(); // id -> job result
const exits = new Map(); // id -> { code, signal }
const errors = [];

let nextJob = 0;
let active = 0;
let finished = 0;
let zygotePid = null;
let finalizing = false;

const zygote = fork(path.join(__dirname, 'zygote.cjs'), [], {
  execArgv: FLAGS,
  env: { ...process.env, ZYGOTE_ADDON: addonPath },
  stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
});

const zygoteExit = new Promise((resolve) => {
  zygote.once('exit', (code, signal) => resolve({ code, signal }));
});

const overallTimer = setTimeout(() => {
  checks.noHang = false;
  fail('overall timeout (60s)');
}, 60000);

zygote.on('error', (err) => fail('zygote error: ' + err.message));

zygote.on('message', (msg, handle) => {
  if (!msg || typeof msg !== 'object') return;
  if (msg.type === 'ready') {
    zygotePid = msg.pid;
    checks.zygoteReady = msg.pid > 0;
    checks.vitestPreloaded = msg.vitest === true;
    pump();
  } else if (msg.type === 'spawned') {
    onSpawned(msg, handle);
  } else if (msg.type === 'exit') {
    onExit(msg);
  } else if (msg.type === 'spawn-error') {
    fail(`spawn error for job ${msg.id}: ${msg.error}`);
  }
});

function pump() {
  while (!finalizing && active < CONCURRENCY && nextJob < N) {
    const job = jobs[nextJob++];
    active++;
    zygote.send({ type: 'spawn', id: job.id, env: job.env });
  }
}

const ctrace = process.env.ZYGOTE_TRACE === '1' ? (...a) => fs.writeSync(2, `[c] ${a.join(' ')}\n`) : () => {};
function onSpawned(msg, handle) {
  ctrace('spawned', msg.id, msg.pid, !!handle);
  const job = jobs[msg.id];
  if (!handle) {
    fail(`no handle passed for job ${msg.id}`);
    return;
  }

  // The received handle is a net.Socket. dup() the fd so we can destroy the
  // socket wrapper without closing the connection, then build a real IPC
  // channel on the duplicate.
  const srcFd = handle._handle ? handle._handle.fd : handle.fd;
  if (typeof srcFd !== 'number' || srcFd < 0) {
    fail(`no fd on handle for job ${msg.id}`);
    return;
  }
  let fd;
  try {
    fd = addon.dupFd(srcFd);
  } catch (err) {
    fail(`dupFd failed for job ${msg.id}: ${err.message}`);
    return;
  }
  handle.destroy();

  const pipe = new Pipe(constants.IPC);
  const rc = pipe.open(fd);
  if (rc !== 0) {
    fail(`pipe.open failed for job ${msg.id}: ${rc}`);
    return;
  }

  const target = new EventEmitter();
  setupChannel(target, pipe, 'advanced');
  target.on('message', (m) => onChildMessage(msg.id, m));
  target.on('error', (err) => {
    errors.push({ id: msg.id, error: String(err) });
  });
  target.on('disconnect', () => {});

  channels.set(msg.id, target);
  spawned.set(msg.id, msg.pid);

  if (job.kill) {
    // Wait until the child is actually running before killing it.
    target.once('message', () => {
      setTimeout(() => {
        try {
          process.kill(msg.pid, 'SIGTERM');
        } catch (err) {
          fail(`kill(${msg.pid}) failed: ${err.message}`);
        }
      }, 150);
    });
  }
}

function onChildMessage(id, msg) {
  ctrace('child msg', id, msg && msg.type);
  if (!msg || typeof msg !== 'object') return;
  if (msg.type === 'zygote-child-ready') {
    if (msg.pid !== spawned.get(id)) {
      fail(`child pid ${msg.pid} != spawned pid ${spawned.get(id)} for job ${id}`);
    }
  } else if (msg.type === 'job-result') {
    results.set(id, msg);
  } else if (msg.type === 'job-error') {
    fail(`job ${id} error: ${msg.error}`);
  } else if (msg.type === 'job-timeout') {
    fail(`job ${id} timed out`);
  }
}

function onExit(msg) {
  ctrace('exit', msg.id, msg.code, msg.signal);
  exits.set(msg.id, { code: msg.code, signal: msg.signal });
  active--;
  finished++;
  if (finished === N) {
    finalize();
  } else {
    pump();
  }
}

function pids() {
  return [...spawned.values()];
}

function zombiesLeft() {
  return pids().filter((pid) => {
    if (fs.existsSync(`/proc/${pid}`)) return true;
    try {
      process.kill(pid, 0);
      return true;
    } catch (err) {
      return err.code !== 'ESRCH';
    }
  });
}

async function finalize() {
  if (finalizing) return;
  finalizing = true;
  clearTimeout(overallTimer);
  checks.noHang = true;

  const allExited = exits.size === N;
  const exitList = [...exits.values()];
  const code0 = exitList.filter((e) => e.code === 0 && e.signal === null).length;
  const code7 = exitList.filter((e) => e.code === 7 && e.signal === null).length;
  const sig15 = exitList.filter((e) => e.signal === 15).length;

  const nonKilled = jobs.filter((j) => !j.kill).map((j) => results.get(j.id));
  const allResults = nonKilled.length === N - 1 && nonKilled.every(Boolean);

  checks.allJobsSpawned = spawned.size === N;
  checks.allPidsDistinct = new Set(pids()).size === N;
  checks.pidsDifferFromZygote = pids().every((p) => p !== zygotePid);
  checks.handleForEveryJob = channels.size === N;
  checks.allJobsExited = allExited;
  checks.exitCodesCorrect = code0 === N - 2 && code7 === 1 && sig15 === 1;
  checks.nonKilledResultsComplete = allResults;

  if (allResults) {
    checks.childPidMatchesSpawned = nonKilled.every(
      (r) => r.pid === spawned.get(r.id),
    );
    checks.childPpidIsZygote = nonKilled.every((r) => r.ppid === zygotePid);
    // The zygote overrides Node's bootstrap-cached process.pid in each child.
    checks.processPidMatchesFork = nonKilled.every(
      (r) => r.processPid === r.pid,
    );
    checks.processPpidIsZygote = nonKilled.every(
      (r) => r.processPpid === zygotePid,
    );
    checks.gcObserved = nonKilled.every(
      (r) => r.forcedGc && r.gc && r.gc.major >= 1 && r.gc.minor >= 1,
    );
    checks.threadpoolWorked = nonKilled.every(
      (r) => r.fsBytes > 0 && r.pbkdf2Bytes === 32,
    );
    checks.advancedSerialization = nonKilled.every(
      (r) =>
        r.map instanceof Map &&
        r.map.get('alpha') === 1 &&
        typeof r.bigint === 'bigint' &&
        r.bigint === 123456789012345678901234567890n,
    );
    checks.promiseAndTimerWorked = nonKilled.every(
      (r) => r.timeoutOk === true && r.promiseValue === 42,
    );
  }

  // No live or zombie children of the zygote before shutdown.
  // Scan /proc rather than task/*/children, which some kernels and PRoot lack.
  const zygoteChildren = fs.readdirSync('/proc').filter((name) => /^\d+$/.test(name)).filter((name) => {
    try {
      const stat = fs.readFileSync(`/proc/${name}/stat`, 'utf8');
      return Number(stat.slice(stat.lastIndexOf(')') + 2).split(' ')[1]) === zygotePid;
    } catch {
      return false;
    }
  });
  checks.zygoteNoChildren = zygoteChildren.length === 0;
  checks.noZombies = zombiesLeft().length === 0;

  try {
    zygote.send({ type: 'shutdown' });
  } catch {
    // already gone
  }

  const exit = await Promise.race([
    zygoteExit,
    new Promise((resolve) => setTimeout(() => resolve(null), 5000)),
  ]);
  checks.zygoteCleanExit =
    exit !== null && exit.code === 0 && exit.signal === null;

  // Re-check after shutdown as well.
  checks.noZombies = checks.noZombies && zombiesLeft().length === 0;

  const ok = Object.values(checks).every((v) => v === true);
  const verdict = {
    ok,
    checks,
    zygote: {
      pid: zygotePid,
      controllerPid: process.pid,
      flags: FLAGS,
      exit,
    },
    jobs: jobs.map((j) => ({
      id: j.id,
      env: j.env,
      pid: spawned.get(j.id) ?? null,
      exit: exits.get(j.id) ?? null,
      result: results.has(j.id)
        ? {
            pid: results.get(j.id).pid,
            ppid: results.get(j.id).ppid,
            processPid: results.get(j.id).processPid,
            gc: results.get(j.id).gc,
            fsBytes: results.get(j.id).fsBytes,
            pbkdf2Bytes: results.get(j.id).pbkdf2Bytes,
            promiseValue: results.get(j.id).promiseValue,
            elapsedMs: Math.round(results.get(j.id).elapsedMs),
          }
        : null,
    })),
    errors,
  };

  fs.writeSync(1, JSON.stringify(verdict, null, 2) + '\n');
  process.exit(ok ? 0 : 1);
}

function fail(message) {
  errors.push(message);
  if (!finalizing) {
    checks.noHang = false;
    finalizing = true;
    clearTimeout(overallTimer);
    try { zygote.kill('SIGKILL'); } catch {}
    for (const pid of pids()) {
      try { process.kill(pid, 'SIGKILL'); } catch {}
    }
    fs.writeSync(
      1,
      JSON.stringify({ ok: false, checks, errors }, null, 2) + '\n',
    );
    process.exit(1);
  }
}
