import { expect, test } from 'vitest';
import { concurrentValue } from '../src/subject';

test.concurrent('concurrent case', async () => {
  await Promise.resolve();
  expect(concurrentValue()).toBe(true);
});
