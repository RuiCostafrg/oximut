import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { setTimeout as sleep } from 'node:timers/promises';
import test from 'node:test';

import {
  classifyCpu,
  cpuTicks,
  killTree,
  processTree,
  sampleWindowMs,
} from '../worker-liveness.ts';

// Termux reports 'android'; what matters is the kernel's /proc.
const linux = existsSync('/proc/self/task');

function spawnNode(code) {
  return spawn(process.execPath, ['-e', code], { stdio: ['ignore', 'pipe', 'ignore'] });
}

async function alive(pid) {
  try {
    process.kill(pid, 0);
  } catch {
    return false;
  }
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, 'utf8');
    return !/^[ZX]/.test(stat.slice(stat.lastIndexOf(')') + 2));
  } catch {
    return false;
  }
}

async function until(condition, ms = 5000) {
  const end = performance.now() + ms;
  while (performance.now() < end) {
    if (await condition()) return true;
    await sleep(20);
  }
  return false;
}

test('cpu time that fills a fifth of the window or more is busy, less is idle', () => {
  // 100 ticks per second: 10 ms each.
  const before = { ticks: 10, at: 1000 };
  assert.equal(classifyCpu(before, { ticks: 110, at: 2000 }), 'busy');
  // A looping worker sharing its core with others still counts.
  assert.equal(classifyCpu(before, { ticks: 30, at: 2000 }), 'busy');
  assert.equal(classifyCpu(before, { ticks: 29, at: 2000 }), 'idle');
  assert.equal(classifyCpu(before, { ticks: 10, at: 2000 }), 'idle');
});

test('readings that say too little tell nothing', () => {
  const before = { ticks: 10, at: 1000 };
  assert.equal(classifyCpu(undefined, { ticks: 50, at: 2000 }), undefined);
  assert.equal(classifyCpu(before, undefined), undefined);
  // Taken too close together for 10 ms ticks to mean anything.
  assert.equal(classifyCpu(before, { ticks: 10, at: 1010 }), undefined);
  // A counter that went backwards belongs to another process.
  assert.equal(classifyCpu(before, { ticks: 5, at: 2000 }), undefined);
});

test('the sampling window is a fifth of the deadline, between 100 ms and 2 s, never more', () => {
  assert.equal(sampleWindowMs(60), 60);
  assert.equal(sampleWindowMs(300), 100);
  assert.equal(sampleWindowMs(5000), 1000);
  assert.equal(sampleWindowMs(13_000), 2000);
  assert.equal(sampleWindowMs(600_000), 2000);
});

test('an unreadable process has no cpu time', () => {
  assert.equal(cpuTicks(undefined), undefined);
  assert.equal(cpuTicks(0), undefined);
  assert.equal(cpuTicks(-1), undefined);
  assert.equal(cpuTicks(2 ** 31 - 2), undefined);
});

// Resolves once the child has written a line, so that start-up CPU is behind it.
function ready(child) {
  return new Promise((resolve) => child.stdout.once('data', resolve));
}

test(
  'a looping process is busy and a sleeping one is idle',
  { skip: !linux && 'needs /proc' },
  async () => {
    const looping = spawnNode('console.log("up"); for (;;);');
    const sleeping = spawnNode('console.log("up"); setInterval(() => {}, 1000)');
    try {
      await Promise.all([ready(looping), ready(sleeping)]);
      await sleep(200);
      const sample = (child) => ({ ticks: cpuTicks(child.pid), at: performance.now() });
      const spin = sample(looping);
      const nap = sample(sleeping);
      await sleep(600);
      assert.equal(classifyCpu(spin, sample(looping)), 'busy');
      assert.equal(classifyCpu(nap, sample(sleeping)), 'idle');
    } finally {
      looping.kill('SIGKILL');
      sleeping.kill('SIGKILL');
    }
  },
);

test(
  'a worker stalled on a futex-style wait is idle however long it ran before',
  { skip: !linux && 'needs /proc' },
  async () => {
    // Computes for a while, then blocks the main thread without using the CPU, as a
    // deadlocked fork child does.
    const stalled = spawnNode(
      'const end = Date.now() + 400; while (Date.now() < end); Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0)',
    );
    try {
      await sleep(900);
      const before = { ticks: cpuTicks(stalled.pid), at: performance.now() };
      assert.ok(before.ticks >= 20, 'it did burn CPU before stalling');
      await sleep(500);
      assert.equal(classifyCpu(before, { ticks: cpuTicks(stalled.pid), at: performance.now() }), 'idle');
    } finally {
      stalled.kill('SIGKILL');
    }
  },
);

test(
  'killTree stops a worker and everything it started, at once',
  { skip: !linux && 'needs /proc/<pid>/task/<tid>/children' },
  async () => {
    // A worker that starts a child, which starts its own.
    const worker = spawnNode(
      `const { spawn } = require('node:child_process');
       spawn(process.execPath, ['-e', "require('node:child_process').spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' }); setInterval(() => {}, 1000)"], { stdio: 'ignore' });
       setInterval(() => {}, 1000);`,
    );
    try {
      assert.ok(
        await until(() => processTree(worker.pid).length === 3),
        'worker, child and grandchild appear',
      );
      const tree = processTree(worker.pid);
      assert.equal(tree[0], worker.pid);
      killTree(worker.pid);
      assert.ok(
        await until(async () => {
          for (const pid of tree) if (await alive(pid)) return false;
          return true;
        }),
        `no process of ${tree.join(', ')} survives`,
      );
    } finally {
      worker.kill('SIGKILL');
    }
  },
);

test('killTree reports every process it signals, leaves first, and ignores a missing one', () => {
  const signalled = [];
  killTree(2 ** 31 - 3, (pid) => signalled.push(pid));
  assert.deepEqual(signalled, [2 ** 31 - 3]);
});
