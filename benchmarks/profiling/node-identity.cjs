// Diagnostic preload only. Do not touch IPC, argv, signals, or exit status.
const fs = require('node:fs');
const path = require('node:path');
const { threadId } = require('node:worker_threads');
const file = path.join(process.env.OXIMUT_PROFILE_DIR,
  `node-${process.pid}-${threadId}.json`);
let startTime = null;
try {
  const value = fs.readFileSync('/proc/self/stat', 'utf8').split(') ').pop().split(' ')[19];
  if (typeof value !== 'string' || !/^\d+$/.test(value)) throw Error('Invalid process start time');
  startTime = value;
} catch (error) {
  try { fs.writeSync(2, `Diagnostic Node identity read failed: ${error.message}\n`); }
  catch { /* Diagnostic output must not replace the workload's exit status. */ }
}
const record = {
  pid: process.pid, ppid: process.ppid, threadId,
  argv: process.argv, execArgv: process.execArgv, cwd: process.cwd(),
  started: new Date().toISOString(),
  segmentCapture: process.env.OXIMUT_PROFILE_SEGMENTS === '1'
    ? { method: 'one inspector startup checkpoint', requestedCheckpointDelayMs: 50,
        maxSegmentsPerIsolate: 1, coverage: 'startup partial; duration and tail loss unbounded' }
    : null,
  selfCpuAtPreloadUs: process.cpuUsage(),
  startTime,
};
let initialWriteSucceeded = false;
try {
  fs.writeFileSync(file, JSON.stringify(record, null, 2));
  initialWriteSucceeded = true;
} catch (error) {
  record.initialIdentityWriteFailed = true;
  try { fs.writeSync(2, `Diagnostic Node identity write failed: ${error.message}\n`); }
  catch { /* Diagnostic output must not replace the workload's exit status. */ }
}
if (process.env.OXIMUT_PROFILE_SEGMENTS === '1' && startTime !== null) {
  try {
    require('./node-segments.cjs')(record);
  } catch (error) {
    try {
      record.segmentCaptureError = String(error?.message ?? error) || 'Unknown segment helper failure';
      fs.writeSync(2, `Diagnostic segment capture failed: ${record.segmentCaptureError}\n`);
    } catch { /* Preserve workload status even if diagnostic output fails. */ }
  }
}
process.on('exit', (code) => {
  if (initialWriteSucceeded) {
    record.exitCode = code;
    record.selfCpuAtExitUs = process.cpuUsage();
    record.exitObserved = new Date().toISOString();
  }
  try {
    // Keep the initial incomplete identity if the final write cannot complete.
    fs.writeFileSync(`${file}.tmp`, JSON.stringify(record, null, 2));
    fs.renameSync(`${file}.tmp`, file);
  } catch (error) {
    try { fs.writeSync(2, `Diagnostic Node identity write failed: ${error.message}\n`); }
    catch { /* Diagnostic output must not replace the workload's exit status. */ }
  }
});
