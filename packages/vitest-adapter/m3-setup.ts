import { appendFileSync } from 'node:fs';
import { getCurrentTest } from '@vitest/runner';

// Installed before test module imports; user setup files require full fallback.
// Sync, deduplicated records include hooks and survive fresh-fork termination.
const destination = process.env.OXIMUT_M3_COVERAGE;
if (!destination) throw new Error('Missing Oximut coverage destination');
appendFileSync(destination, `${JSON.stringify({ ready: true })}\n`);
const seen = new Set<string>();
let overflow = false;
Object.defineProperty(globalThis, '__oximut_m3_hit__', {
  configurable: true,
  value(mutantId: string) {
    const test = getCurrentTest();
    const record = JSON.stringify({ mutantId, testId: test?.id ?? null });
    if (seen.has(record) || overflow) return;
    if (seen.size >= 20000) {
      overflow = true;
      appendFileSync(destination, `${JSON.stringify({ incomplete: 'probe-record-limit' })}\n`);
      return;
    }
    seen.add(record);
    appendFileSync(destination, `${record}\n`);
  },
});

// Reading a function's source can observe a mutation without evaluating its
// expression. Hit absence cannot establish no-coverage after that observation.
const nativeToString = Function.prototype.toString;
let reflected = false;
Function.prototype.toString = function reflectedSource() {
  const source = nativeToString.call(this);
  if (!reflected && source.includes('__oximut_m3_hit__')) {
    reflected = true;
    appendFileSync(destination, `${JSON.stringify({ incomplete: 'source-reflection' })}\n`);
  }
  return source;
};
