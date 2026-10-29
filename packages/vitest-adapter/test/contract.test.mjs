import assert from 'node:assert/strict';
import { execFile, fork } from 'node:child_process';
import { once } from 'node:events';
import { cp, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

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
        .replace("from 'vite'", `from ${JSON.stringify(import.meta.resolve('vite'))}`)
        .replace(
          "from './m3-selection.ts'",
          `from '${new URL('../m3-selection.ts', import.meta.url).href}'`,
        );
      const path = join(directory, name);
      await writeFile(path, source);
      const child = fork(path, [], { stdio: ['ignore', 'pipe', 'pipe', 'ipc'] });
      let diagnostics = '';
      child.stderr.on('data', (data) => {
        diagnostics += data;
      });
      const exited = once(child, 'exit');
      try {
        const response = once(child, 'message', { signal: AbortSignal.timeout(5000) });
        child.send({ protocol: 1, id: 1, command: 'initialize', args: {} });
        const [message] = await Promise.race([
          response,
          exited.then(([code, signal]) => {
            throw new Error(
              `Worker exited before version response (${code}, ${signal}): ${diagnostics}`,
            );
          }),
        ]);
        assert.equal(message.id, 1);
        assert.match(message.error, /Unsupported Vitest 4\.1\.11; expected 4\.1\.12/);
      } finally {
        child.kill();
        await exited;
      }
    }
  },
);

test(
  'both workers reject selected non-Node file pragmas before test execution',
  { timeout: 60000 },
  async (t) => {
    const repository = fileURLToPath(new URL('../../../', import.meta.url));
    const directory = await mkdtemp(join(repository, '.oximut-contract-'));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const config = join(directory, 'vitest.config.mjs');
    await writeFile(config, "export default { test: { include: ['*.test.ts'] } };");
    const file = join(directory, 'sample.test.ts');
    const marker = join(directory, 'executed');
    const body = `import { test } from 'vitest'; import { writeFileSync } from 'node:fs';
writeFileSync(${JSON.stringify(marker)}, 'executed'); test('sample', () => {});`;
    for (const name of ['m1-worker.ts', 'm2-worker.ts']) {
      await writeFile(file, `// @vitest-environment node\n${body}`);
      await writeFile(join(directory, 'excluded.test.ts'), `// @vitest-environment jsdom\n${body}`);
      const child = fork(new URL(`../${name}`, import.meta.url), [], {
        stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
      });
      const exited = once(child, 'exit');
      let id = 0;
      const request = {
        root: directory,
        config,
        kind: 'baseline',
        mutation: null,
        selection: { mode: 'files', files: [file], selectedTests: 1, expectedSkipped: 0 },
      };
      const command = async (command, args) => {
        const response = once(child, 'message', { signal: AbortSignal.timeout(15000) });
        child.send({ protocol: 1, id: ++id, command, args });
        const [message] = await response;
        assert.equal(message.id, id);
        return message;
      };
      try {
        assert.equal((await command('initialize', request)).error, undefined);
        for (const pragma of [
          '// @vitest-environment jsdom',
          '/** @jest-environment happy-dom */',
          '// @vitest-environment custom-unsupported',
        ]) {
          await writeFile(file, `${pragma}\n${body}`);
          const response = await command('run', name === 'm1-worker.ts' ? request : { request });
          assert.match(response.error, /Unsupported test environment .*Node is required/);
          await assert.rejects(readFile(marker), { code: 'ENOENT' });
        }
        // An unsupported file outside the selected scope must not block a Node file.
        await writeFile(file, `// @vitest-environment node\n${body}`);
        const response = await command('run', name === 'm1-worker.ts' ? request : { request });
        assert.equal(response.error, undefined);
        assert.equal(response.result.outcome, 'passed');
        assert.equal(await readFile(marker, 'utf8'), 'executed');
        await rm(marker);
        await rm(join(directory, 'excluded.test.ts'));
        await command('close', {});
      } finally {
        child.kill();
        await exited;
      }
    }
  },
);

test(
  'environment contract runs from a checkout path containing spaces',
  { timeout: 60000 },
  async (t) => {
    const directory = await mkdtemp(join(tmpdir(), 'oximut checkout with spaces '));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const repository = fileURLToPath(new URL('../../../', import.meta.url));
    const adapter = join(directory, 'packages/vitest-adapter');
    await mkdir(join(directory, 'packages'));
    await cp(fileURLToPath(new URL('../', import.meta.url)), adapter, { recursive: true });
    await symlink(join(repository, 'node_modules'), join(directory, 'node_modules'), 'dir');
    // Restrict the nested invocation to the environment contract, excluding this
    // portability test so it cannot recurse into another copied checkout.
    const env = { ...process.env };
    delete env.NODE_TEST_CONTEXT;
    const { stdout } = await promisify(execFile)(
      process.execPath,
      [
        '--test',
        '--test-reporter=tap',
        '--test-name-pattern=both workers reject selected non-Node file pragmas before test execution',
        join(adapter, 'test/contract.test.mjs'),
      ],
      { cwd: directory, env, timeout: 45000 },
    );
    assert.match(stdout, /# pass 1\b/);
    assert.match(stdout, /# fail 0\b/);
  },
);
