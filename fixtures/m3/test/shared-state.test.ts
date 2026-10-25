import { expect, test } from 'vitest';
import { readSharedState, transitionSharedState } from '../src/subject';

// The first test executes the mutation but makes no assertion about it. The
// second test observes the state change without calling the mutated function.
// Selecting only the hit test would miss a kill found by the full file run.
test('transition state', () => {
  transitionSharedState();
});

test('observe transitioned state', () => {
  expect(readSharedState()).toBe(true);
});
