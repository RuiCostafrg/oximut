import { beforeEach } from 'vitest';
import { hookValue, setupValue } from '../src/setup-subject';

// Runs before every test file, outside any test.
(globalThis as { setupReady?: number }).setupReady = setupValue();

// Registered by the setup file, run by Vitest inside each test's lifecycle.
beforeEach(() => {
  if (hookValue() !== true) throw new Error('setup hook observed a mutated value');
});
