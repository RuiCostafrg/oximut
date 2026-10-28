// Diagnostic checkpoints only. Never intercept signals or retain the event loop.
const fs = require('node:fs');
const path = require('node:path');
const { Session } = require('node:inspector');
/** Checkpoint one isolate without retaining its event loop or handling signals. */
module.exports = (record) => {
  const session = new Session();
  let timer;
  let sequence = 0;
  let busy = false;
  const stem = path.join(process.env.OXIMUT_PROFILE_DIR,
    `Segment.${record.pid}.${record.threadId}.${record.startTime}`);
  /** Disable instrumentation while preserving the workload's exit behavior. */
  const fail = (error) => {
    clearInterval(timer);
    try { session.disconnect(); } catch { /* Already disconnected. */ }
    try { fs.writeFileSync(`${stem}.segment-error.txt`, String(error.stack || error)); }
    catch { /* Raw stderr remains a fallback. */ }
    try {
      fs.writeSync(2, `Diagnostic segment capture failed: ${error.message}\n`);
    } catch { /* Preserve workload status. */ }
  };
  /** Guard inspector responses and serialization errors in one place. */
  const post = (method, callback) => {
    try {
      session.post(method, (error, result) => {
        if (error) return fail(error);
        try { callback(result); } catch (error) { fail(error); }
      });
    } catch (error) { fail(error); }
  };
  try {
    session.connect();
    post('Profiler.enable', () => post('Profiler.start', () => {
      timer = setInterval(() => {
        if (busy) return;
        busy = true;
        post('Profiler.stop', ({ profile }) => {
          const stoppedAtNs = process.hrtime.bigint().toString();
          const file = `${stem}.${sequence++}.cpuprofile`;
          fs.writeFileSync(`${file}.tmp`, JSON.stringify(profile));
          fs.renameSync(`${file}.tmp`, file);
          post('Profiler.start', () => {
            fs.appendFileSync(`${stem}.segment-events.jsonl`, JSON.stringify({
              profile: path.basename(file), startTime: profile.startTime, endTime: profile.endTime,
              stoppedAtNs, restartedAtNs: process.hrtime.bigint().toString(),
              requestedIntervalMs: 50,
            }) + '\n');
            busy = false;
          });
        });
      }, 50);
      timer.unref();
    }));
  } catch (error) { fail(error); }
};
