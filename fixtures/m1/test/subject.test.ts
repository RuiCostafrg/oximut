import { expect, it } from 'vitest';
import { belowLimit, enabled } from '../src/subject.js';

it('enables the feature', () => {
  expect(enabled()).toBe(true);
});

it('excludes the exact limit', () => {
  expect(belowLimit(10)).toBe(false);
});
