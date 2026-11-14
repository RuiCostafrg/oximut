import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
test('bootstrap source-map frame', () => {
  try { process.exit(7); throw new Error('missing exit guard'); }
  catch (error) {
    appendFileSync("/tmp/oximut-w13-map-registration-local-1/run/events.jsonl", JSON.stringify({type:'bootstrap-stack',pid:process.pid,stack:error.stack})+'\n');
    expect(error.message).toContain('process.exit unexpectedly called with "7"');
    expect(error.stack).toMatch(/base.Cof-UHJf.js:123:9/);
  }
});
