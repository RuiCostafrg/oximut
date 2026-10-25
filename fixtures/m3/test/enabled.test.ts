import { expect, test } from 'vitest';
import { enabled } from '../src/subject';

test('enabled behavior', () => {
  expect(enabled(true)).toBe(true);
});
