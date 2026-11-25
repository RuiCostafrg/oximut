import { expect, test } from 'vitest';
import { isBuffer, isPlainObject, runtimeKind } from '../src/environment';

test('isBuffer recognises buffers', () => {
  expect(isBuffer(Buffer.from('x'))).toBe(true);
  expect(isBuffer(new Uint8Array(1))).toBe(false);
  expect(isBuffer(null)).toBe(false);
});

test('isPlainObject distinguishes plain objects', () => {
  expect(isPlainObject({})).toBe(true);
  expect(isPlainObject(Object.create(null))).toBe(true);
  expect(isPlainObject([])).toBe(false);
  expect(isPlainObject(null)).toBe(false);
  expect(isPlainObject(new Date())).toBe(false);
});

test('runtime kind', () => {
  expect(runtimeKind()).toBe('global');
});
