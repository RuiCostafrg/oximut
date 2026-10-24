import { describe, expect, it } from 'vitest';
import {
  applyDiscount,
  canAccess,
  clamp,
  classifyScore,
  combineFlags,
  describeRange,
  shippingCost,
} from '../src/logic.js';

describe('clamp', () => {
  it('keeps values inside the bounds and returns the nearest bound outside', () => {
    expect(clamp(4, 0, 10)).toBe(4);
    expect(clamp(-1, 0, 10)).toBe(0);
    expect(clamp(11, 0, 10)).toBe(10);
  });
});

describe('classifyScore', () => {
  it('classifies negative, failing, passing, and excellent scores at boundaries', () => {
    expect(classifyScore(-1)).toBe('negative');
    expect(classifyScore(0)).toBe('failing');
    expect(classifyScore(59)).toBe('failing');
    expect(classifyScore(60)).toBe('passing');
    expect(classifyScore(89)).toBe('passing');
    expect(classifyScore(90)).toBe('excellent');
  });
});

describe('applyDiscount', () => {
  it('preserves invalid prices and discounts, and caps full discounts at zero', () => {
    expect(applyDiscount(-5, 20)).toBe(-5);
    expect(applyDiscount(100, 0)).toBe(100);
    expect(applyDiscount(100, 25)).toBe(75);
    expect(applyDiscount(100, 100)).toBe(0);
  });
});

describe('shippingCost', () => {
  it('uses weight tiers and adds the expedited fee', () => {
    expect(shippingCost(0, false)).toBe(0);
    expect(shippingCost(2, false)).toBe(5);
    expect(shippingCost(3, false)).toBe(10);
    expect(shippingCost(10, true)).toBe(17);
    expect(shippingCost(11, true)).toBe(25);
  });
});

describe('canAccess', () => {
  it('requires an adult, a pass, and an active account', () => {
    expect(canAccess(18, true, false)).toBe(true);
    expect(canAccess(17, true, false)).toBe(false);
    expect(canAccess(18, false, false)).toBe(false);
    expect(canAccess(18, true, true)).toBe(false);
  });
});

describe('describeRange and combineFlags', () => {
  it('uses inclusive upper boundary for medium', () => {
    expect(describeRange(9)).toBe('small');
    expect(describeRange(10)).toBe('medium');
    expect(describeRange(20)).toBe('medium');
    expect(describeRange(21)).toBe('large');
  });

  it('combines either true flag', () => {
    expect(combineFlags(false, false)).toBe(false);
    expect(combineFlags(true, false)).toBe(true);
    expect(combineFlags(false, true)).toBe(true);
    expect(combineFlags(true, true)).toBe(true);
  });
});
