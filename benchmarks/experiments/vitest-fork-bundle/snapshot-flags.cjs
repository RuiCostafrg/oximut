// Benchmark-only snapshot launcher. Use with NODE_OPTIONS=--require=<this file>.
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

if (process.env.OXIMUT_SNAPSHOT_READS_DIR && process.argv[1]?.endsWith('/m2-worker.ts')) {
  const fs = require('node:fs');
  const path = require('node:path');
  const seed = path.join(path.dirname(process.env.OXIMUT_SNAPSHOT_BLOB), 'seed.cjs');
  const read = fs.readFileSync;
  const counters = {calls: 0, bytes: 0, readNs: 0};
  fs.readFileSync = function (file, ...args) {
    if (file !== seed) return Reflect.apply(read, this, [file, ...args]);
    const start = process.hrtime.bigint();
    const source = Reflect.apply(read, this, [file, ...args]);
    counters.readNs += Number(process.hrtime.bigint() - start);
    counters.calls++;
    counters.bytes += Buffer.byteLength(source);
    return source;
  };
  require('node:module').syncBuiltinESMExports();
  process.on('exit', () => {
    fs.writeFileSync(path.join(process.env.OXIMUT_SNAPSHOT_READS_DIR, 'seed-reads-' + process.pid + '.json'),
      JSON.stringify({seed, ...counters}), {flag: 'wx'});
  });
}
