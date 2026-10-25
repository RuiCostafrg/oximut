export function enabled(): boolean {
  return true;
}

export function belowLimit(value: number): boolean {
  return value < 10;
}

// Deliberately untested: M1 runs the full suite and reports this as survived.
export function uncalled(): boolean {
  return false;
}
