// Diagnostic preload only. Do not touch IPC, argv, signals, or exit status.
const fs = require('node:fs');
const path = require('node:path');
const { threadId } = require('node:worker_threads');
const file = path.join(process.env.OXIMUT_PROFILE_DIR,
  `node-${process.pid}-${threadId}.json`);
const record = {
  pid: process.pid, ppid: process.ppid, threadId,
  argv: process.argv, execArgv: process.execArgv, cwd: process.cwd(),
  started: new Date().toISOString(),
  segmentCapture: process.env.OXIMUT_PROFILE_SEGMENTS === '1'
    ? { method: 'inspector checkpoints', requestedIntervalMs: 50, coverage: 'partial; gaps and tail loss unbounded' }
    : null,
  selfCpuAtPreloadUs: process.cpuUsage(),
  startTime: fs.readFileSync('/proc/self/stat', 'utf8').split(') ').pop().split(' ')[19],
};
try {
  fs.writeFileSync(file, JSON.stringify(record, null, 2));
} catch (error) {
  try { fs.writeSync(2, `Diagnostic Node identity write failed: ${error.message}\n`); }
  catch { /* Diagnostic output must not replace the workload's exit status. */ }
}
if (process.env.OXIMUT_PROFILE_SEGMENTS === '1') {
  require('./node-segments.cjs')(record);
}
process.on('exit', (code) => {
  record.exitCode = code;
  record.selfCpuAtExitUs = process.cpuUsage();
  record.exitObserved = new Date().toISOString();
  try {
    // Keep the initial incomplete identity if the final write cannot complete.
    fs.writeFileSync(`${file}.tmp`, JSON.stringify(record, null, 2));
    fs.renameSync(`${file}.tmp`, file);
  } catch (error) {
    try { fs.writeSync(2, `Diagnostic Node identity write failed: ${error.message}\n`); }
    catch { /* Diagnostic output must not replace the workload's exit status. */ }
  }
});
