import { expect, test } from 'vitest';
import { scaled } from '../src/plain';

test('scaled triples', () => {
  expect(scaled(2)).toBe(6);
});
