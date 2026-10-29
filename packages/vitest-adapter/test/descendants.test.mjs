import assert from 'node:assert/strict';
import test from 'node:test';
import { hasUnexpectedDescendants } from '../index.mjs';

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
    assert.equal(scans, 2);
    assert.deepEqual(checked, [101, 102]);
    assert.equal(leaked, !['ENOENT', 'ESRCH'].includes(code), String(code));
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
