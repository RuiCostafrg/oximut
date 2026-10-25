import { expect, test } from 'vitest';
import { belowLimit } from '../src/subject';

test('boundary behavior', () => {
  expect(belowLimit(9)).toBe(true);
  expect(belowLimit(10)).toBe(false);
});
