import { expect, test } from 'vitest';
import { shadowed } from '../src/shadowed';

test('shadowed adds one', () => {
  expect(shadowed({ count: 4 })).toBe(5);
});
