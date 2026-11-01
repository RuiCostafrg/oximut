import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';

const preload = resolve(import.meta.dirname, '../compile-cache.cjs');

/** Fork `entry` with the preload, optionally send Vitest's stop, then SIGTERM it. */
async function run(t, entry, stop) {
  const root = await mkdtemp(join(tmpdir(), 'oximut-compile-cache-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const cache = join(root, 'cache');
  const script = join(root, entry);
  await mkdir(resolve(script, '..'), { recursive: true });
  // Listeners run in registration order, so this acknowledgement follows the
  // preload's synchronous flush. Then idle like a worker until signalled.
  await writeFile(
    script,
    `require('node:assert');
process.on('message', (message) => { if (message?.type === 'stop') process.send('stopped'); });
process.send('ready'); setInterval(() => {}, 1000);`,
  );
  const child = fork(script, [], {
    execArgv: ['--require', preload],
    env: { ...process.env, NODE_COMPILE_CACHE: cache },
    stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
  });
  const exited = once(child, 'exit');
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    await exited;
  });
  /** The next message, failing on child errors, early exit or a timeout. */
  const next = (expected) =>
    Promise.race([
      once(child, 'message', { signal: AbortSignal.timeout(10000) }).then(([message]) => {
        assert.equal(message, expected);
      }),
      exited.then(([code, signal]) => {
        throw new Error(`child exited before ${expected} (${code}, ${signal})`);
      }),
    ]);
  await next('ready');
  if (stop) {
    const stopped = next('stopped');
    child.send({ type: 'stop', __vitest_worker_request__: true });
    await stopped;
  }
  child.kill('SIGTERM');
  await exited;
  // Node creates the versioned directory at startup; entries are files.
  const entries = await readdir(cache, { recursive: true, withFileTypes: true }).catch(() => []);
  return entries.filter((entry) => entry.isFile()).length;
}

test(
  'Vitest fork workers flush the compile cache on the stop request',
  { timeout: 30000 },
  async (t) => {
    assert.ok((await run(t, 'node_modules/vitest/dist/workers/forks.js', true)) > 0);
  },
);

test('without the stop request a signalled fork writes no cache', { timeout: 30000 }, async (t) => {
  assert.equal(await run(t, 'node_modules/vitest/dist/workers/forks.js', false), 0);
});

test(
  'other processes inheriting the preload never listen or flush',
  { timeout: 30000 },
  async (t) => {
    assert.equal(await run(t, 'user/child.js', true), 0);
  },
);
