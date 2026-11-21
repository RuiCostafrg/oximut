// Startup cost: zygote fork() versus a fresh `node` process with the same
// warm preload. Each arm launches JOBS trivial jobs, at most CONCURRENCY at a
// time, alternating arms in rounds. Per job it records request-to-ready wall
// time and the child's CPU at ready. Each child sends its ready message when
// its preload is done. For zygote children, getrusage restarts at fork().
// It also records the zygote's own CPU delta, which includes page-table
// copying in fork(). Run with --expose-internals.
import { fork } from 'node:child_process';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { setupChannel } = require('internal/child_process');
const { Pipe, constants } = process.binding('pipe_wrap');
const here = path.dirname(fileURLToPath(import.meta.url));
const addon = require(process.env.ZYGOTE_ADDON || path.join(here, 'build', 'fork.node'));
const JOBS = Number(process.env.JOBS || 40);
const CONCURRENCY = Number(process.env.CONCURRENCY || 4);
const ROUNDS = Number(process.env.ROUNDS || 3);
const FLAGS = ['--single-threaded'];

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};
const cpuMs = (usage) => (usage.user + usage.system) / 1000;

function memory(pid) {
  try {
    const text = fs.readFileSync(`/proc/${pid}/smaps_rollup`, 'utf8');
    const field = (name) => Number(text.match(new RegExp(`^${name}:\\s+(\\d+)`, 'm'))?.[1] ?? NaN) * 1024;
    return { rss: field('Rss'), pss: field('Pss') };
  } catch {
    return null;
  }
}

// Launches jobs with `start(id, onReady)`; `onReady(cpu, pid)` fires when the
// child is ready. The child is then sampled and told to exit.
async function runArm(start) {
  const rows = [];
  let next = 0;
  let active = 0;
  await new Promise((resolve, reject) => {
    const pump = () => {
      if (rows.length === JOBS) return resolve();
      while (active < CONCURRENCY && next < JOBS) {
        const id = next++;
        active++;
        const begin = process.hrtime.bigint();
        start(id, (cpu, pid, finish) => {
          rows.push({ wallMs: Number(process.hrtime.bigint() - begin) / 1e6, cpuMs: cpu, memory: memory(pid) });
          finish();
        }, () => {
          active--;
          pump();
        }, reject);
      }
    };
    pump();
  });
  return rows;
}

function startZygote() {
  return new Promise((resolve, reject) => {
    const zygote = fork(path.join(here, 'zygote.cjs'), [], {
      execArgv: FLAGS,
      env: { ...process.env, ZYGOTE_ADDON: process.env.ZYGOTE_ADDON || path.join(here, 'build', 'fork.node') },
      stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
    });
    const waiting = new Map();
    zygote.on('message', (message, handle) => {
      if (message.type === 'ready') resolve({ zygote, waiting, pid: message.pid });
      else if (message.type === 'spawned') waiting.get(message.id)?.spawned(message, handle);
      else if (message.type === 'exit') waiting.get(message.id)?.exited(message);
      else if (message.type === 'spawn-error') reject(new Error(message.error));
    });
    zygote.on('error', reject);
  });
}

let zygoteId = 0;
// Keep each controller-side channel reachable while its child runs.
const liveChannels = new Map();
function zygoteJob({ zygote, waiting }) {
  return (_, ready, done, fail) => {
    const id = zygoteId++;
    waiting.set(id, {
      spawned(message, handle) {
        const fd = addon.dupFd(handle._handle.fd);
        handle.destroy();
        const pipe = new Pipe(constants.IPC);
        if (pipe.open(fd) !== 0) return fail(new Error('pipe.open failed'));
        const channel = new EventEmitter();
        setupChannel(channel, pipe, 'advanced');
        channel.once('message', (reply) => {
          ready(cpuMs(reply.cpuUsage), message.pid, () => process.kill(message.pid, 'SIGKILL'));
        });
        channel.on('error', () => {});
        liveChannels.set(id, channel);
      },
      exited() {
        waiting.delete(id);
        liveChannels.delete(id);
        done();
      },
    });
    // JOB_HANG keeps the child alive until it is sampled and killed.
    zygote.send({ type: 'spawn', id, env: { JOB_HANG: '1' } });
  };
}

function freshJob(_, ready, done, fail) {
  const child = fork(path.join(here, 'fresh-child.cjs'), [], {
    execArgv: FLAGS,
    stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
    serialization: 'advanced',
  });
  child.once('message', (reply) => ready(cpuMs(reply.cpuUsage), child.pid, () => child.kill('SIGKILL')));
  child.once('exit', done);
  child.once('error', fail);
}

const zygote = await startZygote();
const arms = { zygote: [], fresh: [] };
const zygoteCpu = [];
for (let round = 0; round < ROUNDS; round++) {
  for (const arm of round % 2 ? ['fresh', 'zygote'] : ['zygote', 'fresh']) {
    if (arm === 'zygote') {
      const before = fs.readFileSync(`/proc/${zygote.pid}/stat`, 'utf8');
      arms.zygote.push(...await runArm(zygoteJob(zygote)));
      const after = fs.readFileSync(`/proc/${zygote.pid}/stat`, 'utf8');
      const ticks = (stat) => stat.slice(stat.lastIndexOf(')') + 2).split(' ').slice(11, 13).reduce((a, b) => a + Number(b), 0);
      zygoteCpu.push((ticks(after) - ticks(before)) * 10 / JOBS);
    } else {
      arms.fresh.push(...await runArm(freshJob));
    }
  }
}
zygote.zygote.send({ type: 'shutdown' });

const summary = (rows) => ({
  jobs: rows.length,
  medianWallToReadyMs: median(rows.map((row) => row.wallMs)),
  medianChildCpuAtReadyMs: median(rows.map((row) => row.cpuMs)),
  medianRssMB: median(rows.filter((row) => row.memory).map((row) => row.memory.rss / 2 ** 20)),
  medianPssMB: median(rows.filter((row) => row.memory).map((row) => row.memory.pss / 2 ** 20)),
});
console.log(JSON.stringify({
  node: process.version,
  arch: process.arch,
  concurrency: CONCURRENCY,
  rounds: ROUNDS,
  zygote: { ...summary(arms.zygote), zygoteCpuPerForkMs: median(zygoteCpu) },
  fresh: summary(arms.fresh),
}, null, 2));
