import { openSync, writeSync } from 'node:fs';
import { threadId } from 'node:worker_threads';
import { TestRunner } from 'vitest';
import { createRecorder } from './probe-recorder.ts';

// Installed before the user's setup files and test module imports. A hit is attributed
// to the test that is running (`TestRunner.getCurrentTest()`): hooks, including ones a
// setup file registers, run inside their test's lifecycle. A hit while no test runs
// (setup-file or test-file module load, beforeAll, suite bodies) has a null identity, so
// the mutant is recorded as outside-test execution and runs the full suite. The probe's
// bookkeeping, which must stay cheap in hot loops, is in `probe-recorder.ts`.
const directory = process.env.OXIMUT_M3_COVERAGE;
if (!directory) throw new Error('Missing Oximut coverage destination');
// A fresh process/thread owns each stream; no cross-worker append races.
const destination = `${directory}/${process.pid}-${threadId}.jsonl`;
const descriptor = openSync(destination, 'a');
// Fake timers may replace the global; the original is what ends the turn.
const queueTask = globalThis.queueMicrotask.bind(globalThis);

function write(text: string): void {
  const bytes = Buffer.from(text);
  let written = 0;
  while (written < bytes.length)
    written += writeSync(descriptor, bytes, written, bytes.length - written);
}
write(`${JSON.stringify({ ready: true })}\n`);

const recorder = createRecorder({
  currentTest: () => TestRunner.getCurrentTest(),
  write,
  schedule: queueTask,
});
Object.defineProperty(globalThis, '__oximut_m3_hit__', {
  configurable: true,
  value: recorder.hit,
});

// Reading a function's source can observe a mutation without evaluating its
// expression. Hit absence cannot establish no-coverage after that observation.
const nativeToString = Function.prototype.toString;
let reflected = false;
Function.prototype.toString = function reflectedSource() {
  const source = nativeToString.call(this);
  if (!reflected && source.includes('__oximut_m3_hit__')) {
    reflected = true;
    recorder.incomplete('source-reflection');
  }
  return source;
};
// A last batch is still pending only if the process ends within the turn that hit it.
process.once('exit', recorder.flush);
