import { expect, test } from 'vitest';
import { beta } from '../src/setup-subject';

test('beta subtracts three', () => {
  expect(beta(5)).toBe(2);
});
