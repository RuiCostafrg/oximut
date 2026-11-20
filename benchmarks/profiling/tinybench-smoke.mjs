import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const dependencies = resolve('node_modules');
const fixture = mkdtempSync(join(tmpdir(), 'oximut-tinybench-smoke-'));
try {
  symlinkSync(dependencies, join(fixture, 'node_modules'), 'dir');
  writeFileSync(join(fixture, 'default-provider.bench.mjs'), `
    import { expect, test } from 'vitest';

    test('built-in benchmark provider', async ({ bench }) => {
      expect(process.send).toBeTypeOf('function');
      const result = await bench('tinybench smoke', () => Math.sqrt(4))
        .run({ time: 10, iterations: 8, warmup: false });
      expect(result.state).toBe('completed');
      expect(result.name).toBe('tinybench smoke');
      expect(result.totalTime).toBeGreaterThan(0);
      expect(result.latency.mean).toBeGreaterThan(0);
    });
  `);
  const run = spawnSync(process.execPath, [join(dependencies, 'vitest/vitest.mjs'), 'bench', '--run',
    '--pool=forks', '--maxWorkers=1', '--reporter=verbose', '--no-color'], { cwd: fixture, encoding: 'utf8', timeout: 30_000 });
  process.stdout.write(run.stdout ?? '');
  process.stderr.write(run.stderr ?? '');
  if (run.error) throw run.error;
  assert.equal(run.status, 0, run.signal ?? 'Vitest benchmark failed');
  assert.match(run.stdout, /built-in benchmark provider/);
} finally {
  rmSync(fixture, { recursive: true, force: true });
}
