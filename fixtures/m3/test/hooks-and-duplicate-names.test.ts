import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test } from 'vitest';
import { hookOnlyValue, hookValue, suiteHookValue, suiteInitializationValue } from '../src/subject';

let beforeAllRan = false;
let afterAllRan = false;
let suiteHookValueResult = false;

beforeAll(() => {
  beforeAllRan = true;
  suiteHookValueResult = suiteHookValue();
});
afterAll(() => { afterAllRan = true; });
beforeEach(() => {
  expect(beforeAllRan).toBe(true);
  expect(hookOnlyValue()).toBe(true);
});
afterEach(() => { expect(hookValue()).toBe(true); });

describe('left suite', () => {
  test('duplicate label', () => {
    expect(hookValue()).toBe(true);
  });
});

describe('right suite', () => {
  test('duplicate label', () => {
    expect(hookValue()).toBe(true);
  });
});

describe('suite initialization', () => {
  const initialized = suiteInitializationValue();
  test('captures initialized value', () => {
    expect(initialized).toBe(true);
  });
});

test('suite lifecycle completes', () => {
  expect(beforeAllRan).toBe(true);
  expect(suiteHookValueResult).toBe(true);
  // afterAll runs after this assertion and is intentionally part of the file.
  expect(afterAllRan).toBe(false);
});
