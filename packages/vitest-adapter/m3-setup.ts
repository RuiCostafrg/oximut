import { appendFileSync } from 'node:fs';
import { threadId } from 'node:worker_threads';
import { TestRunner } from 'vitest';

// Installed before the user's setup files and test module imports. A hit is attributed
// to the test that is running (`TestRunner.getCurrentTest()`): hooks, including ones a
// setup file registers, run inside their test's lifecycle. A hit while no test runs
// (setup-file or test-file module load, beforeAll, suite bodies) has a null identity, so
// the mutant is recorded as outside-test execution and runs the full suite.
// Sync, deduplicated records include hooks and survive fresh-fork termination.
const directory = process.env.OXIMUT_M3_COVERAGE;
if (!directory) throw new Error('Missing Oximut coverage destination');
// A fresh process/thread owns each stream; no cross-worker append races.
const destination = `${directory}/${process.pid}-${threadId}.jsonl`;
appendFileSync(destination, `${JSON.stringify({ ready: true })}\n`);
const seen = new Set<string>();
let overflow = false;
Object.defineProperty(globalThis, '__oximut_m3_hit__', {
  configurable: true,
  value(mutantId: string) {
    const test = TestRunner.getCurrentTest();
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
