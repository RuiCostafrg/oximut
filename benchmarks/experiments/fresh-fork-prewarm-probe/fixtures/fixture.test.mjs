import { test, expect, beforeAll, afterAll, vi } from 'vitest';
import fs from 'node:fs';
import local from './local.mjs';
import cjs from 'stateful-cjs';
import esm from 'stateful-esm';

const requestedId = process.env.OXIMUT_PROBE_JOB_ID;
const activeMutant = process.env.OXIMUT_ACTIVE_MUTANT ?? null;
const realm = globalThis.__oximutProbeRealm;
const target = globalThis.__oximutProbeTarget ??= { call() { return 'original'; } };
function observe(phase, extra = {}) {
  fs.appendFileSync(process.env.OXIMUT_PREWARM_OBSERVATIONS, JSON.stringify({
    phase, requestedId, activeMutant, pid: process.pid, realm, ...extra,
  }) + '\n');
}
observe('module');
beforeAll(() => observe('state', {
  local: local.value, cjs: cjs.value, esm: esm.value,
  global: globalThis.__oximutProbeGlobal ?? null,
  env: process.env.OXIMUT_PROBE_DIRTY ?? null, builtin: fs.__oximutProbeDirty ?? null,
  spy: target.call(), fakeClock: Date.now() === 1234,
}));
test('builtin clean', () => expect(fs.__oximutProbeDirty).toBeUndefined());
test('env clean', () => expect(process.env.OXIMUT_PROBE_DIRTY).toBeUndefined());
test('external CJS clean', () => expect(cjs.value).toBe(0));
test('external ESM clean', () => expect(esm.value).toBe(0));
test('global clean', () => expect(globalThis.__oximutProbeGlobal).toBeUndefined());
test('local ESM clean', () => expect(local.value).toBe(0));
test('spy clean', () => expect(target.call()).toBe('original'));
test('timer clean', () => expect(Date.now()).not.toBe(1234));
afterAll(() => {
  local.value++; cjs.value++; esm.value++; globalThis.__oximutProbeGlobal = 'dirty';
  process.env.OXIMUT_PROBE_DIRTY = 'dirty'; fs.__oximutProbeDirty = 'dirty';
  vi.spyOn(target, 'call').mockReturnValue('dirty'); vi.useFakeTimers(); vi.setSystemTime(1234);
});
