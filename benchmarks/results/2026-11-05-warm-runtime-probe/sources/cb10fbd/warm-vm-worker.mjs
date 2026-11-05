// Pinned experimental VM entrypoint, preserving the dispatcher across stop/start.
import { parentPort } from 'node:worker_threads';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { init } from 'vitest/worker';
import fs from 'node:fs';

const require = createRequire(import.meta.url);
const pkg = require('vitest/package.json');
if (pkg.version !== '4.1.12' || !parentPort) throw new Error('Unsupported VM probe runtime');
const file = join(dirname(require.resolve('vitest/package.json')), 'dist/chunks/vm.CXMd5FHa.js');
const { r: runVmTests, s: setup } = await import(pathToFileURL(file));
if (typeof runVmTests !== 'function' || typeof setup !== 'function') throw new Error('Unsupported VM worker entry');
let dispatcher;
const callbacks = new Set();
let jobSnapshot;
let guardEvidence;
function snapshotHost() {
  const env = new Map(Object.entries(process.env));
  const descriptors = new Map(Reflect.ownKeys(fs).map(key => [key, Object.getOwnPropertyDescriptor(fs, key)]));
  return { env, descriptors };
}
function descriptorSame(a, b) {
  if (!a || !b) return a === b;
  return a.configurable === b.configurable && a.enumerable === b.enumerable &&
    a.writable === b.writable && a.value === b.value && a.get === b.get && a.set === b.set;
}
function compareHost(before) {
  if (!before) return { available: false, dirty: true, envChanges: [], fsChanges: [] };
  const after = snapshotHost();
  const envKeys = new Set([...before.env.keys(), ...after.env.keys()]);
  const envChanges = [...envKeys].filter(key => before.env.get(key) !== after.env.get(key) &&
    // Vitest's VM setup writes SSR=1 after onModuleRunner is called.
    !(key === 'SSR' && before.env.get(key) === undefined && after.env.get(key) === '1'));
  const fsKeys = new Set([...before.descriptors.keys(), ...after.descriptors.keys()]);
  const fsChanges = [...fsKeys].filter(key => !descriptorSame(before.descriptors.get(key), after.descriptors.get(key))).map(String);
  return { available: true, dirty: envChanges.length > 0 || fsChanges.length > 0, envChanges, fsChanges };
}
function releaseRpc() {
  for (const callback of callbacks) if (callback !== dispatcher) {
    parentPort.off('message', callback);
    callbacks.delete(callback);
  }
}
init({
  post(response) {
    if (response?.__vitest_worker_response__ && response.type === 'stopped') {
      guardEvidence = compareHost(jobSnapshot);
      response.__oximutWarmGuard = guardEvidence;
      jobSnapshot = undefined;
      releaseRpc();
    }
    parentPort.postMessage(response);
  },
  on(callback) { dispatcher ??= callback; callbacks.add(callback); parentPort.on('message', callback); },
  onModuleRunner() { jobSnapshot = snapshotHost(); },
  off(callback) { callbacks.delete(callback); parentPort.off('message', callback); },
  setup,
  runTests: (state,traces) => runVmTests('run',state,traces),
  collectTests: (state,traces) => runVmTests('collect',state,traces),
  // Stock init-threads removes ALL message listeners after its stop response.
  // Keep the dispatcher, but release this lifecycle's RPC BEFORE acknowledging.
  teardown() {},
});
