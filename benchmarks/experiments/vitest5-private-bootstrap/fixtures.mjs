import { mkdir, writeFile, symlink } from 'node:fs/promises';
import { join } from 'node:path';
import { repo } from './build.mjs';

export const inventory = ['arithmetic add', 'arithmetic multiply', 'fresh contamination state', 'hooks setup and mocks', 'mapped stack'];
export const jobs = ['inactive', 'A', 'B', 'A'];

export async function makeFixture(root, events) {
  await mkdir(root);
  await symlink(join(repo, 'node_modules'), join(root, 'node_modules'));
  const files = {
    'subject.ts': `const selector = process.env.OXIMUT_ACTIVE_MUTANT;
export const observed = selector;
let calls = 0;
export const count = () => calls++;
export const add = (a: number, b: number) => selector === 'A' ? a + b + 1 : a + b;
export const multiply = (a: number, b: number) => selector === 'B' ? a * b + 1 : a * b;
`,
    'dependency.ts': `export const value = () => 'unmocked';\n`,
    'mapped.ts': `export function mappedThrow(): never {
  throw new Error('mapped failure');
}
`,
    'external/state.cjs': `let n = 0; module.exports = { next() { return n++; } };\n`,
    'external/state.mjs': `let n = 0; export const next = () => n++;\n`,
    'setup.ts': `import { beforeAll, beforeEach, afterEach, afterAll, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
expect((globalThis as any).__privateSetup).toBeUndefined();
(globalThis as any).__privateSetup = { count: 1, before: 0, after: 0, all: 0 };
appendFileSync(${JSON.stringify(events)}, JSON.stringify({type:'setup', pid:process.pid, realm:randomUUID(), selector:process.env.OXIMUT_ACTIVE_MUTANT, scenario:process.env.PROBE_SCENARIO})+'\\n');
beforeAll(() => { (globalThis as any).__privateSetup.all++; });
beforeEach(() => { (globalThis as any).__privateSetup.before++; });
afterEach(() => { (globalThis as any).__privateSetup.after++; });
afterAll(() => {
  appendFileSync(${JSON.stringify(events)}, JSON.stringify({type:'hooks', pid:process.pid, counts:(globalThis as any).__privateSetup})+'\\n');
});
`,
    'state.test.ts': `import { test, expect, vi, afterAll } from 'vitest';
import { appendFileSync } from 'node:fs';
import * as fs from 'node:fs';
import { setTimeout as realTimeout } from 'node:timers';
import cjs from './external/state.cjs';
import { next } from './external/state.mjs';
import { add, multiply, count, observed } from './subject';
import { mappedThrow } from './mapped';
import { value } from './dependency';
vi.mock('./dependency', () => ({value: vi.fn(() => 'mocked')}));
const atImport = process.env.OXIMUT_ACTIVE_MUTANT;
const state = (globalThis as any).__privateSetup;
test('arithmetic add', () => { expect(observed).toBe(atImport); expect(add(1,2)).toBe(3); });
test('arithmetic multiply', () => { expect(multiply(2,3)).toBe(6); });
test('fresh contamination state', () => {
  expect(count()).toBe(0); expect(cjs.next()).toBe(0); expect(next()).toBe(0);
  expect((globalThis as any).__privateDirty).toBeUndefined();
  expect(process.env.PROBE_DIRTY).toBeUndefined();
  expect((fs.readFileSync as any).__privateDirty).toBeUndefined();
  expect(vi.isFakeTimers()).toBe(false); expect(Date.now()).not.toBe(123456789);
  (globalThis as any).__privateDirty = atImport;
  process.env.PROBE_DIRTY = atImport;
  (fs.readFileSync as any).__privateDirty = atImport;
});
test('hooks setup and mocks', () => {
  expect(state.count).toBe(1); expect(state.all).toBe(1);
  expect(state.before).toBe(4); expect(state.after).toBe(3);
  expect(vi.isMockFunction(value)).toBe(true); expect(value()).toBe('mocked');
  const obj = { method: () => 'original' };
  vi.spyOn(obj, 'method').mockReturnValue('spied'); expect(obj.method()).toBe('spied');
});
test('mapped stack', () => {
  try { mappedThrow(); throw new Error('missing throw'); }
  catch (error) {
    expect(error.message).toBe('mapped failure');
    expect(error.stack).toMatch(/mapped.ts:2:9/);
    appendFileSync(${JSON.stringify(events)}, JSON.stringify({type:'stack', pid:process.pid, stack:error.stack})+'\\n');
  }
});
afterAll(() => {
  realTimeout(() => appendFileSync(${JSON.stringify(events)}, JSON.stringify({type:'late',pid:process.pid})+'\\n'), 1000).unref();
  vi.useFakeTimers(); vi.setSystemTime(123456789);
  appendFileSync(${JSON.stringify(events)}, JSON.stringify({type:'dirtied-clock',pid:process.pid,now:Date.now()})+'\\n');
});
`,
    'framework-stack.test.ts': `import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
test('bootstrap source-map frame', () => {
  try { process.exit(7); throw new Error('missing exit guard'); }
  catch (error) {
    appendFileSync(${JSON.stringify(events)}, JSON.stringify({type:'bootstrap-stack',pid:process.pid,stack:error.stack})+'\\n');
    expect(error.message).toContain('process.exit unexpectedly called with "7"');
    expect(error.stack).toMatch(/base.Cof-UHJf.js:123:9/);
  }
});
`,
    'throw.test.ts': `import { test } from 'vitest';
test('intentional throw', () => { throw new Error('intentional test throw'); });
`,
    'import-throw.test.ts': `throw new Error('intentional import throw');\n`,
    'deadline.test.ts': `import { spawn } from 'node:child_process';
import { appendFileSync } from 'node:fs';
import { test } from 'vitest';
test('deadline with descendant', () => {
 const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio:'ignore'});
 appendFileSync(${JSON.stringify(events)}, JSON.stringify({type:'descendant',pid:child.pid,ppid:process.pid})+'\\n');
 while (true) {}
});
`,
  };
  for (const [name, source] of Object.entries(files)) {
    const path = join(root, name);
    await mkdir(join(path, '..'), { recursive: true });
    await writeFile(path, source);
  }
  return files;
}
