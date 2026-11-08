import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { run, mutants } from './probe.mjs';
const here = fileURLToPath(new URL('.', import.meta.url));

test('one controller, duplicate file across projects, fresh forks, local bail, oracle parity', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'shared-controller-'));
  try {
    const oracle = {};
    for (const id of mutants) {
      const output = join(temporary, `${id}.json`);
      await promisify(execFile)(process.execPath, [join(here, 'probe.mjs'), '--oracle', id, output], { timeout: 60000 });
      const result = JSON.parse(await readFile(output, 'utf8'));
      assert.deepEqual(result.errors, []);
      Object.assign(oracle, result.outcomes);
    }
    const shared = await run(mutants);
    assert.deepEqual(shared.errors, []);
    assert.deepEqual(shared.outcomes, oracle);
    assert.equal(shared.outcomes.killed.outcome, 'killed');
    assert.equal(shared.outcomes.timeout.outcome, 'timeout');
    assert.equal(shared.outcomes.survived.outcome, 'survived');
    assert.equal(shared.peak, 2);
    const stock = await run(mutants, 2, { stock: true });
    assert.deepEqual(stock.errors, []);
    assert.deepEqual(stock.outcomes, oracle);
    const starts = shared.events.filter(event => event.type === 'start');
    assert.equal(starts.length, 4);
    assert.equal(new Set(starts.map(event => event.pid)).size, 4);
    assert.equal(shared.events.filter(event => event.type === 'stop').length, 4);
    await writeFile(join(here, 'evidence.json'), JSON.stringify({ node: process.version, vitest: '4.1.12', oracle, shared, stock }, null, 2) + '\n');
  } finally { await rm(temporary, { recursive: true, force: true }); }
});
