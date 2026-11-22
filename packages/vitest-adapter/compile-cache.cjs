'use strict';
// Oximut loads this into Vitest fork workers only. Node writes its compile cache
// at a normal exit, but Vitest stops forks with SIGTERM after their teardown, so
// they never would. Flush once when the worker receives Vitest's stop request.
// User processes that inherit execArgv run a different entrypoint and skip this.
const { flushCompileCache, getCompileCacheDir } = require('node:module');

if (
  getCompileCacheDir() &&
  /[\\/]vitest[\\/]dist[\\/]workers[\\/]forks\.js$/.test(process.argv[1] ?? '')
) {
  process.on('message', (message) => {
    if (message?.__vitest_worker_request__ === true && message.type === 'stop') flushCompileCache();
  });
}
