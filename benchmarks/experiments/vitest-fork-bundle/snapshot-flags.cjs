// Local feasibility probe only. Use with NODE_OPTIONS=--require=<this file>.
if (process.env.OXIMUT_SNAPSHOT_BLOB && process.argv[1]?.endsWith('/m2-worker.ts')) {
  const cp = require('node:child_process');
  const original = cp.fork;
  cp.fork = function (entry, args, options) {
    if (entry.endsWith('/vitest/dist/workers/forks.js')) {
      options = {
        ...options,
        execArgv: [...options.execArgv, '--snapshot-blob=' + process.env.OXIMUT_SNAPSHOT_BLOB],
      };
    }
    return Reflect.apply(original, this, [entry, args, options]);
  };
  require('node:module').syncBuiltinESMExports();
}
