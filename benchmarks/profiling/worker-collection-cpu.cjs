// Sample one eighth of the mutation identities, from run to first collection.
// The surrounding qualification verifies the selector and fork inventory.
const directory = process.env.OXIMUT_COLLECTION_CPU_DIR;
const selector = process.env.OXIMUT_ACTIVE_MUTANT;
if (directory && /^[0-9a-f]{64}$/.test(selector) && selector.slice(0, 2) < '20'
    && process.argv[1]?.endsWith('/vitest/dist/workers/forks.js') && typeof process.send === 'function') {
  const { Session } = require('node:inspector');
  const { writeFileSync } = require('node:fs');
  const { join } = require('node:path');
  let attempted = false;
  let session;
  let completedProfile;
  function command(method, args = {}) {
    let result;
    session.post(method, args, (error, value) => {
      if (!error) result = value;
    });
    if (!result) throw new Error('Local inspector did not complete ' + method);
    return result;
  }

  process.on('message', (message) => {
    if (attempted || message?.__vitest_worker_request__ !== true || message.type !== 'run') return;
    attempted = true;
    try {
      session = new Session();
      session.connect();
      command('Profiler.enable');
      command('Profiler.setSamplingInterval', {interval: 500});
      command('Profiler.start');
    } catch {
      session?.disconnect();
      session = undefined;
    }
  });

  const send = process.send;
  process.send = function (message, ...args) {
    if (session && message?.t === 'q' && message.m === 'onCollected') {
      try {
        completedProfile = command('Profiler.stop').profile;
      } catch { /* Qualification rejects a missing profile. */ }
      finally {
        session.disconnect();
        session = undefined;
      }
    }
    if (completedProfile && message?.__vitest_worker_response__ === true && message.type === 'testfileFinished') {
      try {
        writeFileSync(join(directory, 'collection-cpu-' + process.pid + '.cpuprofile'),
          JSON.stringify(completedProfile), {flag: 'wx'});
      } catch { /* Qualification rejects a missing profile. */ }
      completedProfile = undefined;
    }
    return Reflect.apply(send, this, [message, ...args]);
  };
}
