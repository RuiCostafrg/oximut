import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { hasUnexpectedDescendants, openController } from '../index.mjs';

test('controller reuse independently checks every observed PID after an empty rescan', async () => {
  for (const code of ['ENOENT', 'ESRCH', 'EACCES', 'EIO', null]) {
    let scans = 0;
    const checked = [];
    const leaked = await hasUnexpectedDescendants(
      async () => (++scans === 1 ? [101, 102] : []),
      async (pid) => {
        checked.push(pid);
        if (pid === 101) throw Object.assign(new Error('gone'), { code: 'ENOENT' });
        if (code) throw Object.assign(new Error(code), { code });
      },
    );
    const gone = ['ENOENT', 'ESRCH'].includes(code);
    assert.equal(scans, gone ? 3 : 2, String(code));
    assert.deepEqual(checked, [101, 102]);
    assert.equal(leaked, !gone, String(code));
  }
});

test('descendant scan failures propagate and nonempty scans prevent reuse', async () => {
  const denied = new Error('scan unavailable');
  let scans = 0;
  const check = async () => assert.fail('PID check must not run');
  assert.equal(await hasUnexpectedDescendants(async () => [], check), false);
  assert.equal(
    await hasUnexpectedDescendants(async () => (++scans === 1 ? [101] : [102]), check),
    true,
  );
  for (const failAt of [1, 2]) {
    scans = 0;
    await assert.rejects(
      hasUnexpectedDescendants(async () => {
        if (++scans === failAt) throw denied;
        return [101];
      }, check),
      (error) => error === denied,
    );
  }
});

test('a PID check resolving false means another generation holds the PID', async () => {
  let scans = 0;
  const checked = [];
  const leaked = await hasUnexpectedDescendants(
    async () => (++scans === 1 ? [[101, 'a'], [102, 'b']] : []),
    async (item) => { checked.push(item); return false; },
  );
  assert.deepEqual(checked, [[101, 'a'], [102, 'b']]);
  assert.equal(scans, 3);
  assert.equal(leaked, false);
});

test('any check resolution other than false is a leak', async () => {
  for (const value of [true, undefined, null, 0, '', 'x', {}]) {
    let scans = 0;
    const leaked = await hasUnexpectedDescendants(
      async () => (++scans === 1 ? [[101, 'a']] : []),
      async () => value,
    );
    assert.equal(leaked, true, String(value));
  }
});

test('a late fork seen by the final scan after all observed are gone is a leak', async () => {
  let scans = 0;
  const checked = [];
  const leaked = await hasUnexpectedDescendants(
    async () => (++scans === 1 ? [[101, 'a']] : scans === 3 ? [[202, 'late']] : []),
    async (item) => { checked.push(item); return false; },
  );
  assert.equal(scans, 3);
  assert.deepEqual(checked, [[101, 'a']]);
  assert.equal(leaked, true);
});

test('a throwing checkpoint propagates and stops further scans and checks', async () => {
  const expired = new Error('deadline');
  // Checkpoints: before scan 1, after scan 1, before each of two checks, before the final scan.
  for (let failAt = 1; failAt <= 5; failAt++) {
    let calls = 0;
    let scans = 0;
    let after = 0;
    await assert.rejects(
      hasUnexpectedDescendants(
        async () => { if (calls >= failAt) after++; return ++scans === 1 ? [[101, 'a'], [102, 'b']] : []; },
        async () => { if (calls >= failAt) after++; return false; },
        () => { if (++calls === failAt) throw expired; },
      ),
      (error) => error === expired,
      String(failAt),
    );
    assert.equal(calls, failAt);
    assert.equal(after, 0, `work after checkpoint ${failAt}`);
  }
  // Five checkpoints on the all-gone path; the empty-scan shortcut only reaches two.
  for (const [observed, expected] of [[[[1, 'a'], [2, 'b']], 5], [[], 2]]) {
    let scans = 0;
    let total = 0;
    await hasUnexpectedDescendants(async () => (++scans === 1 ? observed : []), async () => false, () => { total++; });
    assert.equal(total, expected);
  }
});

// The fixture worker learns the report path from its parent's argv, waits for the
// supervisor's .ready marker, then makes cleanup.json a dangling symlink so the real
// supervisor's final report write fails (that write is outside its .error handler).
// For 'error' it also plants a .error diagnostic in the supervisor's documented format.
const sabotageWorker = (diagnostic, target) => `
import { existsSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
// The supervisor (our parent) was started as: python3 supervisor.py node worker <report>.
const report = readFileSync('/proc/' + process.ppid + '/cmdline', 'utf8').split('\\0').filter(Boolean).at(-1);
process.on('message', async (m) => {
  while (!existsSync(report + '.ready')) await new Promise((r) => setTimeout(r, 5));
  symlinkSync(${JSON.stringify(target)}, report);
  if (${JSON.stringify(diagnostic)}) writeFileSync(report + '.error', JSON.stringify({ error: ${JSON.stringify(diagnostic)} }));
  process.send({ protocol: 1, id: m.id, result: { versions: {}, controllerPid: process.pid, reuse: {} } });
});
setInterval(() => {}, 1000);
`;

test('missing cleanup evidence surfaces the supervisor failure, never success, and is cached', { timeout: 30000 }, async () => {
  const dir = await mkdtemp(join(tmpdir(), 'oximut-evidence-test-'));
  try {
    for (const mode of ['noerror', 'error']) {
      const target = join(dir, 'missing-parent', 'cleanup.json');
      const workerFile = join(dir, `${mode}.mjs`);
      await writeFile(workerFile, sabotageWorker(mode === 'error' ? 'Boom: injected' : '', target));
      const controller = await openController({ workerFile });
      const error = await controller.terminate().then(() => assert.fail('terminate must not fabricate evidence'), (e) => e);
      assert.match(error.message, /^Supervisor cleanup failed without evidence: /, mode);
      assert.equal(error.cause.code, 'ENOENT', mode);
      // A planted .error diagnostic wins; otherwise fall back to the captured output tail.
      if (mode === 'error') assert.equal(error.message, 'Supervisor cleanup failed without evidence: Boom: injected');
      else assert.match(error.message, /FileNotFoundError|ENOENT/);
      // Later calls rethrow the same failure instead of re-reading or succeeding.
      await assert.rejects(controller.close(), (e) => e === error, mode);
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
});
