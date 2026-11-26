import { expect, test } from 'vitest';
import { alpha } from '../src/setup-subject';

test('setup file ran before the test file', () => {
  expect((globalThis as { setupReady?: number }).setupReady).toBe(2);
});

test('alpha doubles', () => {
  expect(alpha(4)).toBe(8);
});
