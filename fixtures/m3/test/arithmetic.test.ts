import { expect, test } from 'vitest';
import { adjusted } from '../src/subject';

test('arithmetic behavior', () => {
  expect(adjusted(3)).toBe(5);
});
