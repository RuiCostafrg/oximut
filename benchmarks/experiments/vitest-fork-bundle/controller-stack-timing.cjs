// Benchmark-only instrumentation for the pinned Vitest controller.
if (process.argv[1]?.endsWith('/m2-worker.ts')) {
  const assert = require('node:assert/strict');
  const { writeFileSync } = require('node:fs');
  const { registerHooks } = require('node:module');
  const { join } = require('node:path');
  const directory = process.env.OXIMUT_STACK_TIMING_DIR;
  const mode = process.env.OXIMUT_STACK_TIMING_MODE;
  assert(directory && ['measure', 'skip'].includes(mode));
  const stats = { mode, loaded: 0, calls: 0, errors: 0, wallNs: 0, userUs: 0, systemUs: 0 };
  globalThis[Symbol.for('oximut.diagnostic.controllerStackMaps')] = (update, mapStacks) => {
    stats.calls++;
    for (const [, result] of update) stats.errors += result?.errors?.length ?? 0;
    if (mode === 'skip') return;
    const wall = process.hrtime.bigint();
    const cpu = process.threadCpuUsage();
    try {
      return mapStacks();
    } finally {
      stats.wallNs += Number(process.hrtime.bigint() - wall);
      const elapsed = process.threadCpuUsage(cpu);
      stats.userUs += elapsed.user;
      stats.systemUs += elapsed.system;
    }
  };
  registerHooks({
    load(url, context, nextLoad) {
      const result = nextLoad(url, context);
      if (!url.endsWith('/node_modules/vitest/dist/chunks/cli-api.COfZo8lK.js')) return result;
      const source = String(result.source);
      const method = '\tsyncUpdateStacks(update) {\n';
      const nextMethod = '\n\tasync reportEvent(id, event, data) {';
      const begin = source.indexOf(method) + method.length;
      const end = source.indexOf(nextMethod);
      assert(source.includes(method) && end > begin && source.slice(end - 3, end) === '\n\t}',
        'Unexpected Vitest TestRun stack mapping implementation');
      stats.loaded++;
      return { ...result, source: source.slice(0, begin) +
        "\t\treturn globalThis[Symbol.for('oximut.diagnostic.controllerStackMaps')](update, () => {\n" +
        source.slice(begin, end - 3) + '\n\t\t});\n\t}' + source.slice(end) };
    },
  });
  process.on('exit', () => {
    writeFileSync(join(directory, 'stack-timing-' + process.pid + '.json'),
      JSON.stringify({ pid: process.pid, ...stats }), { flag: 'wx' });
  });
}
