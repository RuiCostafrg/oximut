import { expect, test } from 'vitest';
import { initializedValue } from '../src/subject';

test('import-time value', () => {
  expect(initializedValue()).toBe(1);
});
