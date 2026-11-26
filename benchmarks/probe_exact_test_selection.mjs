// Execute the existing Vitest exact-ID adapter against the sequential-state fixture.
import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { once } from 'node:events';
import { cp, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const repository = fileURLToPath(new URL('..', import.meta.url));
const scratch = await mkdtemp(join(tmpdir(), 'oximut-exact-selection-'));

async function check(root) {
  const child = fork(join(repository, 'packages/vitest-adapter/worker.mjs'), [], {
    stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
  });
  const exited = once(child, 'exit');
  // A broken adapter must leave a failed probe, even when it never sends an IPC reply.
  const deadline = setTimeout(() => child.kill('SIGKILL'), 30_000);
  let id = 0;
  async function command(name, args) {
    const response = once(child, 'message');
    child.send({ protocol: 1, id: ++id, command: name, args });
    const [message] = await Promise.race([response, exited.then(([code, signal]) => {
      throw new Error(`Adapter exited during ${name} (${code ?? signal})`);
    })]);
    assert.equal(message.id, id);
    assert.equal(message.error, undefined, message.error);
    return message.result;
  }
  try {
    await command('initialize', { root });
    const { tests } = await command('discover');
    const stateTests = tests.filter(test => test.file === 'test/shared-state.test.ts');
    assert.equal(stateTests.length, 2);
    const transition = stateTests.find(test => test.name === 'transition state');
    assert(transition, JSON.stringify(stateTests));

    const full = await command('run', { testIds: stateTests.map(test => test.id) });
    const narrow = await command('run', { testIds: [transition.id] });
    assert.deepEqual(narrow.tests.filter(test => test.state !== 'skipped').map(test => test.id), [transition.id]);
    await command('close');
    child.disconnect();
    const [code, signal] = await exited;
    assert.deepEqual([code, signal], [0, null]);
    return { wholeFile: full.outcome, hitTestOnly: narrow.outcome };
  } finally {
    clearTimeout(deadline);
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    await exited;
  }
}

try {
  await cp(join(repository, 'fixtures/m3'), scratch, { recursive: true });
  await symlink(join(repository, 'node_modules'), join(scratch, 'node_modules'), 'dir');
  const original = await check(scratch);
  const sourcePath = join(scratch, 'src/subject.ts');
  const source = await readFile(sourcePath, 'utf8');
  const assignment = 'sharedState = true;';
  assert.equal(source.split(assignment).length, 2);
  await writeFile(sourcePath, source.replace(assignment, 'sharedState = false;'));
  const mutant = await check(scratch);
  assert.deepEqual(original, { wholeFile: 'passed', hitTestOnly: 'passed' });
  assert.deepEqual(mutant, { wholeFile: 'failed', hitTestOnly: 'passed' });
  console.log(JSON.stringify({ original, mutant }, null, 2));
} finally {
  await rm(scratch, { recursive: true, force: true });
}
