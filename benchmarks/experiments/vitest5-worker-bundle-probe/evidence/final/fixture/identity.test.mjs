import { test, expect } from 'vitest';
import { appendFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
const selector = process.env.OXIMUT_ACTIVE_MUTANT;
appendFileSync("/tmp/oximut-v5-bundle-attempt-6/observations.jsonl", JSON.stringify({pid:process.pid,realm:randomUUID(),selector})+'\n');
test('collector identity and import-time selector', () => { expect(['','A','B']).toContain(selector); });
