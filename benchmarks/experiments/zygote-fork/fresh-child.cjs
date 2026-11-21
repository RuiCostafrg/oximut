'use strict';
// Control arm for measure.mjs: the zygote's warm preload in a fresh process.
const builtins = ['fs', 'path', 'os', 'util', 'crypto', 'v8', 'perf_hooks', 'events',
  'stream', 'net', 'child_process', 'buffer', 'url', 'assert', 'timers'];
for (const name of builtins) require(name);
import('vitest').catch(() => {}).then(() => {
  process.send({ type: 'ready', cpuUsage: process.cpuUsage() });
  setInterval(() => {}, 30000);
});
