import { expect, it } from 'vitest';
import { add, belowLimit } from '../src/subject.js';

// A suite that takes longer than a short fixed deadline: every mutant of the subject
// runs this file, so the deadline of its job has to follow how long the file takes.
it('takes its time and still checks the limit', async () => {
  await new Promise((resolve) => setTimeout(resolve, 2500));
  expect(belowLimit(2)).toBe(true);
  expect(belowLimit(3)).toBe(false);
  expect(add(1, 2)).toBe(3);
});
