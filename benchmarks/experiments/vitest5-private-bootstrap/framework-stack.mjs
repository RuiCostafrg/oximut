// Additional gate using the SAME candidate artifact, never a repaired/rebuilt worker.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { hash, manifest } from './build.mjs';
import { invoke } from './probe.mjs';
import { makeFixture } from './fixtures.mjs';

const [prior, output] = process.argv.slice(2).map(path => resolve(path));
assert.ok(prior && output && process.argv.length === 4, 'Usage: node --experimental-import-meta-resolve framework-stack.mjs PRIOR_CAPTURE NEW_OUTPUT');
await mkdir(output);
const evidence = { status: 'incomplete', prior, performance: 'unmeasured', results: {} };
try {
  const previous = JSON.parse(await readFile(join(prior, 'evidence.json'), 'utf8'));
  assert.equal(previous.status, 'local structural matrix passed; native qualification pending');
  const bundle = previous.bundle.path;
  evidence.bundle = previous.bundle;
  assert.equal(hash(await readFile(bundle)), previous.bundle.sha256, 'Candidate changed');
  assert.equal(hash(await readFile(bundle + '.map')), previous.bundle.mapSha256, 'Candidate map changed');
  evidence.sources = await manifest(import.meta.dirname);
  const root = join(output, 'fixture'), events = join(output, 'events.jsonl');
  await makeFixture(root, events);
  const source = `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
test('bootstrap source-map frame', () => {
  try { process.exit(7); throw new Error('missing exit guard'); }
  catch (error) {
    appendFileSync(${JSON.stringify(events)}, JSON.stringify({type:'bootstrap-stack',pid:process.pid,stack:error.stack})+'\\n');
    expect(error.message).toContain('process.exit unexpectedly called with "7"');
    expect(error.stack).toMatch(/base.Cof-UHJf.js:123:/);
  }
});
`;
  await writeFile(join(root, 'framework-stack.test.ts'), source);
  evidence.fixtureSha256 = hash(source);
  for (const mode of ['stock','candidate']) {
    evidence.results[mode] = await invoke({ root, events, scenario: 'framework-stack', bundle: mode === 'candidate' ? bundle : undefined }, join(output, mode));
  }
  assert.equal(hash(await readFile(join(root, 'framework-stack.test.ts'))), evidence.fixtureSha256);
  assert.equal(hash(await readFile(bundle)), previous.bundle.sha256);
  evidence.status = 'bootstrap stack fidelity passed';
} catch (error) {
  evidence.status = 'structural failure'; evidence.failure = { message: error.message, stack: error.stack }; process.exitCode = 1;
} finally {
  await writeFile(join(output, 'evidence.json'), JSON.stringify(evidence, null, 2) + '\n');
}
console.log(JSON.stringify({ output, status: evidence.status, failure: evidence.failure?.message }));
