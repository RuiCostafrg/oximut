'use strict';
// Child workload. Runs inside a forked child, after the new IPC channel is up.
//
// It deliberately exercises: the event loop (setTimeout), the libuv threadpool
// (fs.readFile, crypto.pbkdf2), V8 GC (200 MB churn + a forced major GC), the
// Promise machinery, and 'advanced' IPC serialization (Map + BigInt).

const fs = require('fs');
const crypto = require('crypto');
const v8 = require('v8');
const { promisify } = require('util');
const { PerformanceObserver, constants } = require('perf_hooks');

const pbkdf2 = promisify(crypto.pbkdf2);
const CHUNK = 131072; // 128K numbers * 8 bytes = 1 MiB per array
const CHUNKS = 200; // ~200 MiB of old-space pressure

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function heap() {
  const h = v8.getHeapStatistics();
  return {
    used: h.used_heap_size,
    total: h.total_heap_size,
    external: h.external_memory,
  };
}

const jt = process.env.ZYGOTE_TRACE === '1' ? (m) => require('fs').writeSync(2, `[j ${require('fs').readlinkSync('/proc/self')}] ${m}\n`) : () => {};
module.exports = async function runJob({ id, env, addon }) {
  jt('start');
  const startedAt = process.hrtime.bigint();
  const exitCode = env.JOB_EXIT === '7' ? 7 : 0;

  if (env.JOB_HANG === '1') {
    // Controller kills this one with SIGTERM; the timer keeps the loop alive.
    await new Promise(() => setTimeout(() => {}, 30000));
  }

  if (env.JOB_TRIVIAL === '1') {
    await delay(5);
    const result = {
      type: 'job-result',
      id,
      pid: addon.getpid(),
      ppid: addon.getppid(),
      processPid: process.pid,
      processPpid: process.ppid,
      trivial: true,
      elapsedMs: Number(process.hrtime.bigint() - startedAt) / 1e6,
    };
    await send(result);
    addon._exitNow(exitCode);
  }

  const gcEntries = [];
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      gcEntries.push({
        kind: entry.detail ? entry.detail.kind : null,
        duration: entry.duration,
      });
    }
  });
  observer.observe({ entryTypes: ['gc'] });

  // 1. Force scavenges and ~200 MiB of allocation.
  const before = heap();
  const keep = [];
  for (let i = 0; i < CHUNKS; i++) {
    keep.push(new Array(CHUNK).fill(i));
    // Some short-lived garbage so the scavenger has something to do too.
    for (let j = 0; j < 200; j++) ({ i, j, s: 'garbage' + j });
  }
  const allocatedBytes = CHUNKS * CHUNK * 8;
  keep.length = 0;

  jt('allocated');
  // 2. Force a synchronous major GC (requires --expose-gc).
  let forcedGc = false;
  if (typeof global.gc === 'function') {
    try {
      global.gc({ type: 'major', execution: 'sync' });
    } catch {
      global.gc();
    }
    forcedGc = true;
  }
  const after = heap();

  jt('gc done');
  // 3. Event loop.
  const t0 = Date.now();
  await delay(20);
  const timeoutOk = Date.now() - t0 >= 15;

  jt('timer done');
  // 4. libuv threadpool: async fs and crypto.
  const data = await fs.promises.readFile(__filename);
  jt('readFile done');
  const key = await pbkdf2('zygote', 'fork', 50000, 32, 'sha256');

  jt('pbkdf2 done');
  // 5. Promise chain.
  let promiseValue = 0;
  await Promise.resolve(1)
    .then((x) => x + 1)
    .then((x) => x * 2)
    .then((x) => x + 10)
    .then((x) => x * 3)
    .then((x) => (promiseValue = x));

  // Let PerformanceObserver deliver the GC entries.
  await delay(50);
  observer.disconnect();

  const counts = {
    minor: gcEntries.filter((e) => e.kind === constants.NODE_PERFORMANCE_GC_MINOR)
      .length,
    major: gcEntries.filter((e) => e.kind === constants.NODE_PERFORMANCE_GC_MAJOR)
      .length,
    incremental: gcEntries.filter(
      (e) => e.kind === constants.NODE_PERFORMANCE_GC_INCREMENTAL,
    ).length,
    weakcb: gcEntries.filter((e) => e.kind === constants.NODE_PERFORMANCE_GC_WEAKCB)
      .length,
  };

  const result = {
    type: 'job-result',
    id,
    pid: addon.getpid(),
    ppid: addon.getppid(),
    processPid: process.pid,
    processPpid: process.ppid,
    forcedGc,
    gc: { ...counts, total: gcEntries.length },
    heap: { before, after },
    allocatedBytes,
    timeoutOk,
    fsBytes: data.length,
    pbkdf2Bytes: key.length,
    promiseValue,
    map: new Map([
      ['alpha', 1],
      ['beta', 2],
    ]),
    bigint: 123456789012345678901234567890n,
    elapsedMs: Number(process.hrtime.bigint() - startedAt) / 1e6,
    cpuUsage: process.cpuUsage(),
  };

  jt('sending');
  await send(result);
  addon._exitNow(exitCode);
};

function send(message) {
  return new Promise((resolve) => {
    process.send(message, undefined, undefined, () => resolve());
  });
}
