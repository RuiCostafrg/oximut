import { test, expect, vi, afterAll } from 'vitest';
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
    appendFileSync("/tmp/oximut-w13-private-attempt-1/run/events.jsonl", JSON.stringify({type:'stack', pid:process.pid, stack:error.stack})+'\n');
  }
});
afterAll(() => {
  realTimeout(() => appendFileSync("/tmp/oximut-w13-private-attempt-1/run/events.jsonl", JSON.stringify({type:'late',pid:process.pid})+'\n'), 1000).unref();
  vi.useFakeTimers(); vi.setSystemTime(123456789);
  appendFileSync("/tmp/oximut-w13-private-attempt-1/run/events.jsonl", JSON.stringify({type:'dirtied-clock',pid:process.pid,now:Date.now()})+'\n');
});
