import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

// Exercise each entrypoint's early version gate with a stub, without installing
// or claiming compatibility with another Vitest version.
test(
  'both workers reject an unpinned Vitest before creating a controller',
  { timeout: 15000 },
  async (t) => {
    const directory = await mkdtemp(join(tmpdir(), 'oximut-version-contract-'));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const stub =
      'data:text/javascript,' +
      encodeURIComponent(
        "export const version = '4.1.11'; export function createVitest() { throw new Error('must not initialize'); }",
      );
    for (const name of ['m1-worker.ts', 'm2-worker.ts']) {
      const original = await readFile(new URL(`../${name}`, import.meta.url), 'utf8');
      const source = original
        .replace("from 'vitest/node'", `from ${JSON.stringify(stub)}`)
        .replace(
          "from './m3-selection.ts'",
          `from '${new URL('../m3-selection.ts', import.meta.url).href}'`,
        );
      const path = join(directory, name);
      await writeFile(path, source);
      const child = fork(path, [], { stdio: ['ignore', 'pipe', 'pipe', 'ipc'] });
      const exited = once(child, 'exit');
      try {
        const response = once(child, 'message', { signal: AbortSignal.timeout(5000) });
        child.send({ protocol: 1, id: 1, command: 'initialize', args: {} });
        const [message] = await response;
        assert.equal(message.id, 1);
        assert.match(message.error, /Unsupported Vitest 4\.1\.11; expected 4\.1\.12/);
      } finally {
        child.kill();
        await exited;
      }
    }
  },
);
