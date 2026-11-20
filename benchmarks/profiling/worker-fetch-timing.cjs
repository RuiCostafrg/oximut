// Optional benchmark preload, pinned to Vitest 5.1.0's fork/RPC protocol.
const dir = process.env.OXIMUT_PROFILE_COLLECTION_DIR;
if (dir && process.env.OXIMUT_BUDGETED === '1'
    && process.argv[1]?.endsWith('/vitest/dist/workers/forks.js')
    && typeof process.send === 'function') {
  const fs = require('node:fs');
  const path = require('node:path');
  const vm = require('node:vm');
  const now = () => process.hrtime.bigint();
  const methods = new Set(['fetch', 'fetchModule', 'fetchWarmModules', 'resolve']);
  const calls = new Map();
  const spans = [];
  const evaluation = [];
  const compiles = [];
  let runAt;
  let collectionAt;

  const originalCompile = vm.runInThisContext;
  vm.runInThisContext = function (source, ...args) {
    // Stock Vitest evaluator's synchronous compile boundary, from
    // dist/module-evaluator.js. The returned function and invocation are stock.
    if (!runAt || collectionAt || !source?.startsWith?.("'use strict';async (__vite_ssr_exports__,")) {
      return Reflect.apply(originalCompile, this, [source, ...args]);
    }
    const start = now();
    const cpu = process.threadCpuUsage();
    try {
      return Reflect.apply(originalCompile, this, [source, ...args]);
    } finally {
      compiles.push({wallMs: Number(now() - start) / 1e6, cpuUs: process.threadCpuUsage(cpu)});
    }
  };

  const responseKind = (message) => {
    if (message.e) return 'error';
    if (message.r?.cache === true) return 'alreadyEvaluated';
    if (message.r?.cached === true) return 'tmpFile';
    if (typeof message.r?.externalize === 'string') return 'external';
    if (typeof message.r?.code === 'string') return 'inline';
    return 'other';
  };

  process.on('message', (message) => {
    if (message?.__vitest_worker_request__ === true && message.type === 'run') runAt ??= now();
    if (!runAt || collectionAt || message?.t !== 's') return;
    const call = calls.get(message.i);
    if (!call) return;
    const end = now();
    calls.delete(message.i);
    const snapshotEntries = call.method === 'fetchWarmModules' && message.r
      ? Object.keys(message.r).length : null;
    spans.push({...call, end, responseKind: responseKind(message), snapshotEntries});
  });

  const originalSend = process.send;
  process.send = function (message, ...args) {
    if (runAt && !collectionAt && message?.t === 'q') {
      if (methods.has(message.m) && typeof message.i === 'string') {
        calls.set(message.i, {method: message.m, start: now(),
          ...(process.env.OXIMUT_PROFILE_FETCH_DIR ? {rpc: message.i} : {})});
      } else if (message.m === 'onCollected') {
        collectionAt = now();
      }
    }
    if (collectionAt && !saved && message?.__vitest_worker_response__ === true && message.type === 'testfileFinished') {
      // Write after first collection. Capture the evaluator map at the first
      // onCollected send below, before later files can replace its entries.
      const record = {
        pid: process.pid, ppid: process.ppid,
        runToFirstCollectionMs: Number(collectionAt - runAt) / 1e6,
        outstanding: calls.size,
        spans: spans.map(({start, end, ...span}) => ({...span,
          startMs: Number(start - runAt) / 1e6, endMs: Number(end - runAt) / 1e6})),
        compiles, evaluation,
      };
      const file = path.join(dir, `collection-${process.ppid}-${process.pid}.json`);
      if (!saved) {
        saved = true;
        try { fs.writeFileSync(file, JSON.stringify(record), {flag: 'wx'}); }
        catch { /* A missing record fails capture validation. */ }
      }
    }
    const result = Reflect.apply(originalSend, this, [message, ...args]);
    if (collectionAt && !collected) {
      collected = true;
      const metrics = globalThis.__vitest_worker__?.moduleExecutionInfo;
      if (metrics instanceof Map) {
        for (const {external, duration, selfTime} of metrics.values()) {
          evaluation.push({kind: external ? 'external' : 'inline', duration, selfTime});
        }
      }
    }
    return result;
  };
  let saved = false;
  let collected = false;
}
