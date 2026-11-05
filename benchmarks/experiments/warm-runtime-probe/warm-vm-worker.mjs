// Pinned experimental VM entrypoint, preserving the dispatcher across stop/start.
import { parentPort } from 'node:worker_threads';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { init } from 'vitest/worker';

const require = createRequire(import.meta.url);
const pkg = require('vitest/package.json');
if (pkg.version !== '4.1.12' || !parentPort) throw new Error('Unsupported VM probe runtime');
const file = join(dirname(require.resolve('vitest/package.json')), 'dist/chunks/vm.CXMd5FHa.js');
const { r: runVmTests, s: setup } = await import(pathToFileURL(file));
if (typeof runVmTests !== 'function' || typeof setup !== 'function') throw new Error('Unsupported VM worker entry');
let dispatcher;
const callbacks = new Set();
function releaseRpc() {
  for (const callback of callbacks) if (callback !== dispatcher) {
    parentPort.off('message', callback);
    callbacks.delete(callback);
  }
}
init({
  post(response) {
    if (response?.__vitest_worker_response__ && response.type === 'stopped') releaseRpc();
    parentPort.postMessage(response);
  },
  on(callback) { dispatcher ??= callback; callbacks.add(callback); parentPort.on('message', callback); },
  off(callback) { callbacks.delete(callback); parentPort.off('message', callback); },
  setup,
  runTests: (state,traces) => runVmTests('run',state,traces),
  collectTests: (state,traces) => runVmTests('collect',state,traces),
  // Stock init-threads removes ALL message listeners after its stop response.
  // Keep the dispatcher, but release this lifecycle's RPC BEFORE acknowledging.
  teardown() {},
});
