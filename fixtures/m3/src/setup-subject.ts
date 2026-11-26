// Exercised through a setup file: `setupValue` while the setup file loads (no test is
// running), `hookValue` from a beforeEach the setup file registers (inside every test).
// `alpha` and `beta` are reached only from their own test file.
export function setupValue(): number {
  return 1 + 1;
}

export function hookValue(): boolean {
  return true;
}

export function alpha(value: number): number {
  return value * 2;
}

export function beta(value: number): number {
  return value - 3;
}
