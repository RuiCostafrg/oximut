import { beforeAll, beforeEach, afterEach, afterAll, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
expect((globalThis as any).__privateSetup).toBeUndefined();
(globalThis as any).__privateSetup = { count: 1, before: 0, after: 0, all: 0 };
appendFileSync("/tmp/oximut-w13-map-registration-local-1/run/events.jsonl", JSON.stringify({type:'setup', pid:process.pid, realm:randomUUID(), selector:process.env.OXIMUT_ACTIVE_MUTANT, scenario:process.env.PROBE_SCENARIO})+'\n');
beforeAll(() => { (globalThis as any).__privateSetup.all++; });
beforeEach(() => { (globalThis as any).__privateSetup.before++; });
afterEach(() => { (globalThis as any).__privateSetup.after++; });
afterAll(() => {
  appendFileSync("/tmp/oximut-w13-map-registration-local-1/run/events.jsonl", JSON.stringify({type:'hooks', pid:process.pid, counts:(globalThis as any).__privateSetup})+'\n');
});
