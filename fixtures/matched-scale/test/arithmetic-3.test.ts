import { expect, it } from 'vitest';
import { operation0, operation1, operation2, operation3, operation4, operation5, operation6, operation7 } from '../src/arithmetic-3.js';

it('operation 0 at three inputs', () => {
  expect(operation0(-8)).toBeCloseTo(23, 10);
  expect(operation0(0)).toBeCloseTo(31, 10);
  expect(operation0(8)).toBeCloseTo(39, 10);
});

it('operation 1 at three inputs', () => {
  expect(operation1(-8)).toBeCloseTo(-39, 10);
  expect(operation1(0)).toBeCloseTo(-31, 10);
  expect(operation1(8)).toBeCloseTo(-23, 10);
});

it('operation 2 at three inputs', () => {
  expect(operation2(-8)).toBeCloseTo(-248, 10);
  expect(operation2(0)).toBeCloseTo(0, 10);
  expect(operation2(8)).toBeCloseTo(248, 10);
});

it('operation 3 at three inputs', () => {
  expect(operation3(-8)).toBeCloseTo(-0.25806451612903225, 10);
  expect(operation3(0)).toBeCloseTo(0.0, 10);
  expect(operation3(8)).toBeCloseTo(0.25806451612903225, 10);
});

it('operation 4 at three inputs', () => {
  expect(operation4(-8)).toBeCloseTo(24, 10);
  expect(operation4(0)).toBeCloseTo(32, 10);
  expect(operation4(8)).toBeCloseTo(40, 10);
});

it('operation 5 at three inputs', () => {
  expect(operation5(-8)).toBeCloseTo(-40, 10);
  expect(operation5(0)).toBeCloseTo(-32, 10);
  expect(operation5(8)).toBeCloseTo(-24, 10);
});

it('operation 6 at three inputs', () => {
  expect(operation6(-8)).toBeCloseTo(-256, 10);
  expect(operation6(0)).toBeCloseTo(0, 10);
  expect(operation6(8)).toBeCloseTo(256, 10);
});

it('operation 7 at three inputs', () => {
  expect(operation7(-8)).toBeCloseTo(-0.25, 10);
  expect(operation7(0)).toBeCloseTo(0.0, 10);
  expect(operation7(8)).toBeCloseTo(0.25, 10);
});
