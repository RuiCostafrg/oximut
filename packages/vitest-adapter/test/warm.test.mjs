import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { once } from 'node:events';
import test from 'node:test';

test('warm transforms resident and test graphs without evaluating user modules', async (t) => {
  const repository = resolve(import.meta.dirname, '../../..');
  await mkdir(join(repository, '.oximut'), { recursive: true });
  const root = await mkdtemp(join(repository, '.oximut/warm-test-'));
  const observed = join(root, 'evaluated');
  await writeFile(join(root, 'vitest.config.mjs'), "export default { test: { include: ['sample.test.ts'] } };");
  await writeFile(join(root, 'source.ts'), 'export const value = false;');
  await writeFile(join(root, 'dependency.ts'), `import { appendFileSync } from 'node:fs'; appendFileSync(${JSON.stringify(observed)}, 'dependency\\n'); export const value = true;`);
  await writeFile(join(root, 'sample.test.ts'), `import { test, expect } from 'vitest'; import { value } from './source'; import { appendFileSync } from 'node:fs'; appendFileSync(${JSON.stringify(observed)}, process.pid + '\\n'); test('resident', () => expect(value).toBe(true));`);
  const child = fork(join(repository, 'packages/vitest-adapter/m2-worker.ts'), [], { execArgv: [], stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) { const exited = once(child, 'exit'); child.kill('SIGKILL'); await exited; }
    await rm(root, { recursive: true, force: true });
  });
  let id = 0;
  async function command(command, args) {
    const response = once(child, 'message', { signal: AbortSignal.timeout(30000) });
    child.send({ protocol: 1, id: id++, command, args });
    const [reply] = await response;
    assert.equal(reply.error, undefined);
    return reply.result;
  }
  const request = { protocol: 1, runId: 'warm', jobId: 'warm', generation: 0, kind: 'baseline', root,
    config: join(root, 'vitest.config.mjs'), deadlineMs: 30000, mutation: null,
    resident: [{ id: 'resident', file: join(root, 'source.ts'), code: "export { value } from './dependency';", map: { version: 3, sources: ['source.ts'], names: [], mappings: 'AAAA' } }] };
  await command('initialize', request);
  const warmed = await command('warm', { request });
  assert.equal(warmed.warmed, true);
  assert.ok(warmed.transformed >= 3, JSON.stringify(warmed));
  assert.equal(warmed.tests.passed, 0);
  await assert.rejects(readFile(observed), { code: 'ENOENT' });
  const result = await command('run', { request });
  assert.equal(result.outcome, 'passed', JSON.stringify(result));
  const lines = (await readFile(observed, 'utf8')).trim().split('\n');
  assert.equal(lines[0], 'dependency');
  assert.match(lines[1] ?? '', /^\d+$/);
  assert.notEqual(Number(lines[1]), child.pid);
  await command('close', {});
});
