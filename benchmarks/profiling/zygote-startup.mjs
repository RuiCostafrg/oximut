// Replay the recorded Vitest fork launch contract in rotating four-fork waves,
// comparing restored-image workers started as fresh `node` processes with
// workers forked from a restored-image zygote. Each worker gets the recorded
// start request and replies `started`; then it is stopped and terminated.
// No tests run. Requires --expose-internals and ZYGOTE_ADDON (built fork.c).
import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { EventEmitter, once } from 'node:events';
import { readFileSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const require = createRequire(import.meta.url);
const { setupChannel } = require('internal/child_process');
const { Pipe, constants: pipeConstants } = process.binding('pipe_wrap');
const [captureArg, outputArg, roundsArg = '24'] = process.argv.slice(2);
const capture = resolve(captureArg);
const output = resolve(outputArg);
const rounds = Number(roundsArg);
const addonPath = resolve(process.env.ZYGOTE_ADDON ?? '');
const addon = require(addonPath);
assert(Number.isSafeInteger(rounds) && rounds > 0);
assert(!process.env.NODE_OPTIONS && !process.env.NODE_DISABLE_COMPILE_CACHE);
assert.equal(process.versions.node, '26.3.1');
const width = 4;
const sources = (await readdir(capture)).filter((name) => /^startup-contract-\d+\.json$/.test(name)).sort();
assert(sources.length > 0, 'missing runtime launch contract');
const launch = JSON.parse(await readFile(resolve(capture, sources[0])));
const snapshotFlag = launch.execArgv.find((arg) => arg.startsWith('--snapshot-blob='));
assert(snapshotFlag, 'the zygote replay needs a restored-image launch contract');
assert.equal(launch.serialization, 'advanced');
assert.equal(launch.stdio, 'pipe');
assert.match(launch.entry, /\/vitest\/dist\/workers\/forks\.js$/);
const probe = resolve(import.meta.dirname, 'fresh-worker-probe.cjs');
const zygotePreload = resolve(import.meta.dirname, 'zygote-worker.cjs');
const cacheRoot = await mkdtemp(resolve(output, 'cache-'));
const cacheDir = resolve(cacheRoot, 'shared');
await mkdir(cacheDir);
// Both arms run with --single-threaded so that the comparison isolates the fork.
const execArgv = [...launch.execArgv.filter((arg) => arg !== snapshotFlag), snapshotFlag, '--single-threaded'];
const baseEnv = {...process.env, ...launch.env, NODE_COMPILE_CACHE: cacheDir,
  OXIMUT_SNAPSHOT_BLOB: snapshotFlag.slice('--snapshot-blob='.length)};
const time = () => String(process.hrtime.bigint());
const ticks = (pid) => {
  const stat = readFileSync(`/proc/${pid}/stat`, 'utf8');
  return stat.slice(stat.lastIndexOf(')') + 2).split(' ').slice(11, 13).reduce((sum, value) => sum + Number(value), 0);
};

function freshWorker() {
  const child = fork(launch.entry, launch.argv, {
    env: {...baseEnv, NODE_OPTIONS: '--require=' + JSON.stringify(probe)},
    execArgv, cwd: launch.cwd, stdio: launch.stdio, serialization: launch.serialization,
  });
  child.stdout.resume();
  let stderr = '';
  child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
  const exit = once(child, 'exit').then(([code, signal]) => ({code, signal}));
  return Promise.resolve({pid: child.pid, channel: child, exit, kill: (signal) => child.kill(signal),
    stderr: () => stderr});
}

async function startZygote() {
  const zygote = fork(launch.entry, launch.argv, {
    env: {...baseEnv, OXIMUT_ZYGOTE_ADDON: addonPath,
      NODE_OPTIONS: `--require=${JSON.stringify(probe)} --require=${JSON.stringify(zygotePreload)}`},
    execArgv, cwd: launch.cwd, stdio: launch.stdio, serialization: launch.serialization,
  });
  zygote.stdout.resume();
  let stderr = '';
  zygote.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
  const pending = new Map();
  let nextId = 0;
  const readyNs = time();
  const ready = new Promise((resolveReady, reject) => {
    zygote.once('exit', (code, signal) => reject(Error(`zygote exited ${code}/${signal}: ${stderr}`)));
    zygote.on('message', (message, handle) => {
      const kind = message?.__oximut_zygote__;
      if (kind === 'ready') resolveReady(message);
      else if (kind === 'spawned') pending.get(message.id).spawned(message, handle);
      else if (kind === 'exit') pending.get(message.id).exited(message);
    });
  });
  const {pid} = await ready;
  return {
    pid, bootNs: [readyNs, time()], stderr: () => stderr,
    stop: async () => {
      zygote.send({__oximut_zygote__: 'shutdown'});
      const [code, signal] = await once(zygote, 'exit');
      return {code, signal};
    },
    spawn: () => new Promise((resolveSpawn) => {
      const id = nextId++;
      let resolveExit;
      const exit = new Promise((resolveExitPromise) => { resolveExit = resolveExitPromise; });
      pending.set(id, {
        spawned(message, handle) {
          const fd = addon.dupFd(handle._handle.fd);
          handle.destroy();
          const pipe = new Pipe(pipeConstants.IPC);
          assert.equal(pipe.open(fd), 0);
          const channel = new EventEmitter();
          setupChannel(channel, pipe, 'advanced');
          channel.on('error', () => {});
          resolveSpawn({pid: message.pid, channel, exit, stderr,
            kill: (signal) => { try { return process.kill(message.pid, signal); } catch { return false; } }});
        },
        exited({code, signal}) {
          pending.delete(id);
          resolveExit({code, signal});
        },
      });
      zygote.send({__oximut_zygote__: 'spawn', id});
    }),
  };
}

async function sample(kind, round, lane, zygote) {
  const forkNs = time();
  const worker = kind === 'zygote' ? await zygote.spawn() : await freshWorker();
  let exited = false;
  worker.exit.then(() => { exited = true; });
  const send = async (type, message, expected) => {
    const ns = time();
    const receive = Promise.race([once(worker.channel, 'message'), worker.exit.then(({code, signal}) => {
      throw Error(`pid ${worker.pid} exited before ${expected}: ${code}/${signal}: ${worker.stderr()}`);
    })]);
    worker.channel.send({__vitest_worker_request__: true, type, ...message});
    const [response] = await receive;
    const receiptNs = time();
    assert.equal(response.type, expected);
    assert.equal(response.__vitest_worker_response__, true);
    assert.equal(response.error, undefined);
    assert(response.diagnostic?.beforeStart);
    return {sendNs: ns, receiptNs, ...response.diagnostic};
  };
  const timeout = setTimeout(() => { if (!exited) worker.kill('SIGKILL'); }, 15_000);
  try {
    const start = await send('start', launch.start, 'started');
    const stop = await send('stop', {}, 'stopped');
    worker.kill('SIGTERM');
    const {code, signal} = await worker.exit;
    const exitNs = time();
    assert(code === 0 && signal === null || code === null && signal === 'SIGTERM', `${code}/${signal}`);
    return {kind, round, lane, pid: worker.pid, forkNs, start, stop, exitNs, code, signal};
  } finally {
    clearTimeout(timeout);
    // A reaped zygote child's pid may already belong to another process.
    if (!exited) worker.kill('SIGKILL');
    await worker.exit;
  }
}

const records = [];
const zygoteWaves = [];
let zygote;
try {
  zygote = await startZygote();
  const wave = async (kind, round) => {
    const before = kind === 'zygote' ? ticks(zygote.pid) : 0;
    const results = await Promise.all(Array.from({length: width}, (_, lane) => sample(kind, round, lane, zygote)));
    if (kind === 'zygote') zygoteWaves.push({round, forks: width, zygoteTicks: ticks(zygote.pid) - before});
    records.push(...results);
  };
  await wave('fresh', -1);
  await wave('zygote', -1);
  for (let round = 0; round < rounds; round++) {
    for (const kind of round % 2 ? ['zygote', 'fresh'] : ['fresh', 'zygote']) await wave(kind, round);
  }
  const pids = records.map((record) => record.pid);
  assert.equal(new Set(pids).size, pids.length, 'every worker must be a distinct process');
  assert(!pids.includes(zygote.pid));
} finally {
  const zygoteExit = zygote ? await zygote.stop() : null;
  await rm(cacheRoot, {recursive: true, force: true});
  await writeFile(resolve(output, 'zygote-launch-replay.json'), JSON.stringify({
    node: process.version, v8: process.versions.v8, arch: process.arch, execPath: process.execPath,
    execArgv, contracts: sources, width, rounds, clockTicksPerSecond: 100,
    zygote: zygote ? {pid: zygote.pid, bootNs: zygote.bootNs, exit: zygoteExit, stderr: zygote.stderr()} : null,
    zygoteWaves, records,
  }, null, 2) + '\n', {flag: 'wx'});
}
