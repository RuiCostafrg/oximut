import { beforeEach, expect, it } from 'vitest';
import { initialized, reset, count, nested, asynchronous, sequence, boundary } from '../src/subject.js';

beforeEach(reset);
it('evaluates imports with the active mutation and fresh module state', () => {
  expect(initialized).toBe(true);
  expect(count()).toBe(0);
});
it('evaluates nested operands exactly once in source order', () => {
  expect(nested()).toBe(7);
  expect(count()).toBe(3);
});
it('preserves await and generator suspension', async () => {
  expect(await asynchronous()).toBe(5);
  const iterator = sequence();
  expect(iterator.next()).toEqual({ value: 3, done: false });
  expect(iterator.next(4)).toEqual({ value: 6, done: true });
});
it('checks the comparison boundary', () => {
  expect(boundary(3)).toBe(false);
});
