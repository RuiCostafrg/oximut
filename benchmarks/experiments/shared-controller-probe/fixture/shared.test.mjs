import { test, expect, inject } from 'vitest';
import { mutant, value } from './subject.mjs';

test('assignment exists before user module evaluation', async () => {
  expect(mutant).toBe(inject('mutant'));
  // Keep both workers occupied long enough to observe actual overlap.
  await new Promise(resolve => setTimeout(resolve, 150));
});
test('mutation', async () => {
  if (mutant === 'crash') process.kill(process.pid, 'SIGKILL');
  if (mutant === 'hard-timeout') while (true) {}
  if (mutant === 'unhandled') {
    Promise.reject(new Error('unhandled probe'));
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  if (mutant === 'timeout') await new Promise(() => {});
  expect(value()).toBe(1);
});
test('remaining test', () => { expect(value()).toBe(1); });
